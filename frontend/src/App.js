import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import CameraManagement from './components/CameraManagement';
import RecordingControl from './components/RecordingControl';
import DownloadUpload from './components/DownloadUpload';
import LivePreview from './components/LivePreview';
import './App.css';

const API_URL = 'http://127.0.0.1:8000';

function App() {
  const [activeTab, setActiveTab] = useState('cameras');
  const [cameras, setCameras] = useState([]);
  const [ws, setWs] = useState(null);
  const [backendStatus, setBackendStatus] = useState('connecting');
  const [downloadWsMessage, setDownloadWsMessage] = useState(null);
  const downloadWsMsgCounter = useRef(0);
  const [activeShoot, setActiveShoot] = useState(null);

  // Connect to WebSocket for real-time updates
  useEffect(() => {
    connectWebSocket();
    checkBackendStatus();

    // Poll for camera updates (WebSocket handles instant updates)
    const pollInterval = setInterval(() => {
      fetchCameras();
    }, 5000);

    return () => {
      if (ws) {
        ws.close();
      }
      clearInterval(pollInterval);
    };
  }, []);

  const connectWebSocket = () => {
    const websocket = new WebSocket('ws://127.0.0.1:8000/ws');

    websocket.onopen = () => {
      console.log('WebSocket connected');
      setWs(websocket);
    };

    websocket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handleWebSocketMessage(data);
    };

    websocket.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    websocket.onclose = () => {
      console.log('WebSocket closed, reconnecting...');
      setTimeout(connectWebSocket, 3000);
    };
  };

  const handleWebSocketMessage = (data) => {
    console.log('WebSocket message:', data);

    // Handle different message types
    switch (data.type) {
      case 'camera_added':
      case 'camera_removed':
        console.log('Camera list changed, refreshing...');
        fetchCameras();
        break;
      case 'camera_connection':
        console.log(`📡 INSTANT UPDATE: Camera ${data.serial} connection status: ${data.connected}`);
        // Instantly update the camera state without waiting for polling
        setCameras(prevCameras =>
          prevCameras.map(cam =>
            cam.serial === data.serial
              ? { ...cam, connected: data.connected }
              : cam
          )
        );
        break;
      case 'recording_started':
        console.log(`🔴 INSTANT UPDATE: Camera ${data.serial} recording started`);
        setCameras(prevCameras =>
          prevCameras.map(cam =>
            cam.serial === data.serial
              ? { ...cam, recording: data.success }
              : cam
          )
        );
        break;
      case 'recording_stopped':
        console.log(`⏹️ INSTANT UPDATE: Camera ${data.serial} recording stopped`);
        setCameras(prevCameras =>
          prevCameras.map(cam =>
            cam.serial === data.serial
              ? { ...cam, recording: false }
              : cam
          )
        );
        break;
      case 'battery_update':
        // Update battery levels on all cameras
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
        if (activeShoot && activeShoot.id === data.shoot_id) setActiveShoot(null);
        break;
      case 'take_started':
      case 'take_stopped':
        fetchActiveShoot();
        break;
      default:
        break;
    }
  };

  const checkBackendStatus = async () => {
    try {
      const response = await axios.get(`${API_URL}/health`);
      if (response.data.status === 'healthy') {
        setBackendStatus('connected');
        await fetchCameras();
        fetchActiveShoot();

        // Check for existing BLE connections in background (don't await)
        checkExistingConnections();
      }
    } catch (error) {
      setBackendStatus('error');
      setTimeout(checkBackendStatus, 3000);
    }
  };

  const checkExistingConnections = async () => {
    // Run in background without blocking
    try {
      console.log('Checking for existing BLE connections...');
      axios.post(`${API_URL}/api/cameras/check-connections`, null, { timeout: 5000 })
        .then(response => {
          if (response.data.connected_count > 0) {
            console.log(`Found ${response.data.connected_count} existing connections`);
            // Fetch cameras to update UI immediately
            fetchCameras();
          }
        })
        .catch(error => {
          console.error('Check existing connections failed:', error);
        });
    } catch (error) {
      console.error('Check existing connections failed:', error);
    }
  };

  const fetchCameras = async () => {
    try {
      console.log('Fetching cameras from API...');
      const response = await axios.get(`${API_URL}/api/cameras`);
      console.log('Received cameras:', response.data.cameras);
      setCameras(response.data.cameras);
      return response.data.cameras;
    } catch (error) {
      console.error('Failed to fetch cameras:', error);
      return [];
    }
  };

  const fetchActiveShoot = async () => {
    try {
      const response = await axios.get(`${API_URL}/api/shoots/active`);
      setActiveShoot(response.data.shoot);
    } catch (error) {
      console.error('Failed to fetch active shoot:', error);
    }
  };

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
          Recording Control
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
          <RecordingControl
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
