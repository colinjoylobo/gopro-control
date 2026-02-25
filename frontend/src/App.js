import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import CameraManagement from './components/CameraManagement';
import RecordingDashboard from './components/RecordingDashboard';
import DownloadUpload from './components/DownloadUpload';
import LivePreview from './components/LivePreview';
import './App.css';

const API_URL = process.env.REACT_APP_API_URL || 'http://127.0.0.1:8000';
const WS_URL = API_URL.replace(/^http/, 'ws') + '/ws';

function App() {
  const [activeTab, setActiveTab] = useState('cameras');
  const [cameras, setCameras] = useState([]);
  const [backendStatus, setBackendStatus] = useState('connecting');
  const [downloadWsMessage, setDownloadWsMessage] = useState(null);
  const downloadWsMsgCounter = useRef(0);
  const [activeShoot, setActiveShoot] = useState(null);
  const [cohnStatus, setCohnStatus] = useState({});
  const wsSubscribersRef = useRef(new Set());
  const wsRef = useRef(null);
  const wsBackoffRef = useRef(1000);

  const subscribeWsMessages = useCallback((callback) => {
    wsSubscribersRef.current.add(callback);
    return () => wsSubscribersRef.current.delete(callback);
  }, []);

  const fetchCohnStatus = useCallback(async () => {
    try {
      const response = await axios.get(`${API_URL}/api/cohn/status`);
      setCohnStatus(response.data.cameras || {});
    } catch (error) {
      console.error('Failed to fetch COHN status:', error);
    }
  }, []);

  const fetchCameras = useCallback(async () => {
    try {
      const response = await axios.get(`${API_URL}/api/cameras`);
      setCameras(response.data.cameras);
      return response.data.cameras;
    } catch (error) {
      console.error('Failed to fetch cameras:', error);
      return [];
    }
  }, []);

  const fetchActiveShoot = useCallback(async () => {
    try {
      const response = await axios.get(`${API_URL}/api/shoots/active`);
      setActiveShoot(response.data.shoot);
    } catch (error) {
      console.error('Failed to fetch active shoot:', error);
    }
  }, []);

  const handleWebSocketMessage = useCallback((data) => {
    switch (data.type) {
      case 'camera_added':
      case 'camera_removed':
        fetchCameras();
        break;
      case 'camera_connection':
        setCameras(prevCameras =>
          prevCameras.map(cam =>
            cam.serial === data.serial
              ? { ...cam, connected: data.connected }
              : cam
          )
        );
        break;
      case 'recording_started':
        setCameras(prevCameras =>
          prevCameras.map(cam =>
            cam.serial === data.serial
              ? { ...cam, recording: data.success }
              : cam
          )
        );
        break;
      case 'recording_stopped':
        setCameras(prevCameras =>
          prevCameras.map(cam =>
            cam.serial === data.serial
              ? { ...cam, recording: false }
              : cam
          )
        );
        break;
      case 'battery_update':
        if (data.levels) {
          setCameras(prevCameras =>
            prevCameras.map(cam => {
              const level = data.levels[cam.serial];
              if (level !== null && level !== undefined) {
                return { ...cam, battery_level: level };
              }
              return cam;
            })
          );
        }
        break;
      case 'download_status':
      case 'download_progress':
      case 'download_complete':
      case 'download_error':
        downloadWsMsgCounter.current += 1;
        setDownloadWsMessage({ ...data, _seq: downloadWsMsgCounter.current });
        break;
      case 'shoot_created':
      case 'shoot_activated':
        setActiveShoot(data.shoot);
        break;
      case 'shoot_deactivated':
        setActiveShoot(null);
        break;
      case 'shoot_deleted':
        setActiveShoot(prev => (prev && prev.id === data.shoot_id ? null : prev));
        break;
      case 'take_started':
      case 'take_stopped':
        fetchActiveShoot();
        break;
      case 'cohn_camera_online':
      case 'cohn_camera_offline':
        setCohnStatus(prev => ({
          ...prev,
          [data.serial]: {
            ...(prev[data.serial] || {}),
            online: data.online
          }
        }));
        break;
      default:
        break;
    }

    // Broadcast to subscribers
    wsSubscribersRef.current.forEach(cb => {
      try { cb(data); } catch (e) { console.error('WS subscriber error:', e); }
    });
  }, [fetchCameras, fetchActiveShoot]);

  // WebSocket connection with exponential backoff
  useEffect(() => {
    let destroyed = false;
    let reconnectTimer = null;

    const connect = () => {
      if (destroyed) return;
      const websocket = new WebSocket(WS_URL);

      websocket.onopen = () => {
        console.log('WebSocket connected');
        wsRef.current = websocket;
        wsBackoffRef.current = 1000; // reset backoff on success
      };

      websocket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          handleWebSocketMessage(data);
        } catch (e) {
          console.error('WS parse error:', e);
        }
      };

      websocket.onerror = (error) => {
        console.error('WebSocket error:', error);
      };

      websocket.onclose = () => {
        wsRef.current = null;
        if (destroyed) return;
        const delay = wsBackoffRef.current;
        wsBackoffRef.current = Math.min(delay * 2, 30000); // cap at 30s
        console.log(`WebSocket closed, reconnecting in ${delay}ms...`);
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      destroyed = true;
      clearTimeout(reconnectTimer);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [handleWebSocketMessage]);

  // Initial backend check + polling fallback
  useEffect(() => {
    const checkBackendStatus = async () => {
      try {
        const response = await axios.get(`${API_URL}/health`);
        if (response.data.status === 'healthy') {
          setBackendStatus('connected');
          await fetchCameras();
          fetchActiveShoot();
          fetchCohnStatus();

          // Check for existing BLE connections in background
          axios.post(`${API_URL}/api/cameras/check-connections`, null, { timeout: 5000 })
            .then(response => {
              if (response.data.connected_count > 0) {
                fetchCameras();
              }
            })
            .catch(() => {});
        }
      } catch (error) {
        setBackendStatus('error');
        setTimeout(checkBackendStatus, 3000);
      }
    };

    checkBackendStatus();

    // Polling fallback (WebSocket handles instant updates, this is the safety net)
    const pollInterval = setInterval(fetchCameras, 5000);
    return () => clearInterval(pollInterval);
  }, [fetchCameras, fetchActiveShoot, fetchCohnStatus]);

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-content">
          <h1>GoPro Control Center</h1>
          <div className="status-indicator">
            <div className={`status-dot ${backendStatus}`}></div>
            <span>{backendStatus === 'connected' ? 'Connected' : 'Connecting...'}</span>
          </div>
        </div>
      </header>

      <div className="tabs">
        <button
          className={`tab ${activeTab === 'cameras' ? 'active' : ''}`}
          onClick={() => setActiveTab('cameras')}
        >
          Camera Management
        </button>
        <button
          className={`tab ${activeTab === 'recording' ? 'active' : ''}`}
          onClick={() => setActiveTab('recording')}
        >
          Dashboard
        </button>
        <button
          className={`tab ${activeTab === 'preview' ? 'active' : ''}`}
          onClick={() => setActiveTab('preview')}
        >
          Live Preview
        </button>
        <button
          className={`tab ${activeTab === 'download' ? 'active' : ''}`}
          onClick={() => setActiveTab('download')}
        >
          Download & Upload
        </button>
      </div>

      <div className="tab-content">
        {activeTab === 'cameras' && (
          <CameraManagement
            cameras={cameras}
            onCamerasUpdate={fetchCameras}
            apiUrl={API_URL}
          />
        )}
        {activeTab === 'recording' && (
          <RecordingDashboard
            cameras={cameras}
            onCamerasUpdate={fetchCameras}
            apiUrl={API_URL}
            activeShoot={activeShoot}
            onShootUpdate={fetchActiveShoot}
          />
        )}
        {activeTab === 'preview' && (
          <LivePreview
            cameras={cameras}
            apiUrl={API_URL}
            cohnStatus={cohnStatus}
            onCohnUpdate={fetchCohnStatus}
            subscribeWsMessages={subscribeWsMessages}
          />
        )}
        {activeTab === 'download' && (
          <DownloadUpload
            cameras={cameras}
            apiUrl={API_URL}
            downloadWsMessage={downloadWsMessage}
            activeShoot={activeShoot}
          />
        )}
      </div>
    </div>
  );
}

export default App;
