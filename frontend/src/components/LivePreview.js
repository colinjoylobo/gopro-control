import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import Hls from 'hls.js';
import './LivePreview.css';

const LAYOUTS = [
  { id: 'auto', label: 'Auto', icon: '\u229E', css: 'layout-auto' },
  { id: '1up', label: '1-up', icon: '\u25FB', css: 'layout-1up' },
  { id: '2x1', label: '2x1', icon: '\u25EB', css: 'layout-2x1' },
  { id: '2x2', label: '2x2', icon: '\u229E', css: 'layout-2x2' },
  { id: 'pip', label: '1+3 PIP', icon: '\u22A1', css: 'layout-pip' },
  { id: '3x2', label: '3x2', icon: '\u229E', css: 'layout-3x2' },
];

function LivePreview({ cameras, apiUrl, cohnStatus, onCohnUpdate, subscribeWsMessages }) {
  // Mode: 'cohn' or 'single'
  const [mode, setMode] = useState(() => {
    return localStorage.getItem('gopro_preview_mode') || 'single';
  });

  // === Single Camera (WiFi AP) state ===
  const [selectedCamera, setSelectedCamera] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [streamUrl, setStreamUrl] = useState(null);
  const [message, setMessage] = useState(null);
  const [currentWifi, setCurrentWifi] = useState(null);
  const [wifiConnected, setWifiConnected] = useState(false);

  // === Shared state ===
  const [healthData, setHealthData] = useState({});
  const [activeLayout, setActiveLayout] = useState(() => {
    return localStorage.getItem('gopro_preview_layout') || 'auto';
  });
  const [isFullscreen, setIsFullscreen] = useState(false);

  // === COHN state ===
  const [cohnCameras, setCohnCameras] = useState({}); // {serial: {streaming, streamUrl}}
  const [wifiSSID, setWifiSSID] = useState(() => localStorage.getItem('gopro_cohn_ssid') || '');
  const [wifiPassword, setWifiPassword] = useState(() => localStorage.getItem('gopro_cohn_password') || '');
  const [provisioning, setProvisioning] = useState(null); // serial being provisioned
  const [provisionStep, setProvisionStep] = useState('');

  // === Settings & Presets state ===
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsValues, setSettingsValues] = useState(() => {
    const saved = localStorage.getItem('gopro_settings_values');
    if (saved) try { return JSON.parse(saved); } catch(e) {}
    return { resolution: '4K', fps: '60', video_fov: 'Linear', hypersmooth: 'Off', anti_flicker: '60Hz' };
  });
  const [gpsEnabled, setGpsEnabled] = useState(true);
  const [applyingSettings, setApplyingSettings] = useState(false);

  // === Refs ===
  const singleVideoRef = useRef(null);
  const singleHlsRef = useRef(null);
  const videoRefsMap = useRef({}); // {serial: videoElement}
  const hlsInstancesMap = useRef({}); // {serial: hlsInstance}
  const gridRef = useRef(null);

  // Persist mode
  useEffect(() => {
    localStorage.setItem('gopro_preview_mode', mode);
  }, [mode]);

  // Persist layout
  useEffect(() => {
    localStorage.setItem('gopro_preview_layout', activeLayout);
  }, [activeLayout]);

  // Persist COHN WiFi creds
  useEffect(() => {
    localStorage.setItem('gopro_cohn_ssid', wifiSSID);
  }, [wifiSSID]);
  useEffect(() => {
    localStorage.setItem('gopro_cohn_password', wifiPassword);
  }, [wifiPassword]);

  // Persist settings values
  useEffect(() => {
    localStorage.setItem('gopro_settings_values', JSON.stringify(settingsValues));
  }, [settingsValues]);

  // Settings dropdown options
  const SETTINGS_OPTIONS = {
    resolution: ['5.3K', '4K', '2.7K', '1080'],
    fps: ['240', '200', '120', '100', '60', '50', '30', '25', '24'],
    video_fov: ['Wide', 'Linear', 'Narrow', 'SuperView'],
    hypersmooth: ['Off', 'On', 'High', 'Boost'],
    anti_flicker: [
      { value: '60Hz', label: '60Hz (NTSC)' },
      { value: '50Hz', label: '50Hz (PAL)' },
    ],
  };

  const SETTINGS_LABELS = {
    resolution: 'Resolution',
    fps: 'FPS',
    video_fov: 'FOV / Lens',
    hypersmooth: 'HyperSmooth',
    anti_flicker: 'Anti-Flicker',
  };

  const handleApplySettings = async (targetSerials = null) => {
    setApplyingSettings(true);
    const label = targetSerials ? `camera ${targetSerials[0]}` : 'all cameras';
    setMessage({ type: 'info', text: `Applying settings to ${label}...` });
    try {
      const allSettings = { ...settingsValues, gps: gpsEnabled ? 'On' : 'Off' };
      const payload = { settings: allSettings };
      if (targetSerials) payload.serials = targetSerials;
      const response = await axios.post(`${apiUrl}/api/cohn/settings/apply`, payload, { timeout: 30000 });
      const results = response.data.results || {};
      const successCount = Object.values(results).filter(r =>
        Object.values(r).every(v => v.success !== false)
      ).length;
      const totalCount = Object.keys(results).length;
      setMessage({
        type: successCount === totalCount ? 'success' : 'warning',
        text: `Settings applied to ${successCount}/${totalCount} camera(s)`
      });
    } catch (error) {
      setMessage({ type: 'error', text: `Apply settings failed: ${error.response?.data?.detail || error.message}` });
    }
    setApplyingSettings(false);
    setTimeout(() => setMessage(null), 5000);
  };

  const handleToggleGps = async () => {
    const newValue = !gpsEnabled;
    setMessage({ type: 'info', text: `${newValue ? 'Enabling' : 'Disabling'} GPS on all cameras...` });
    try {
      await axios.post(`${apiUrl}/api/cohn/settings/apply`, {
        settings: { gps: newValue ? 'ON' : 'OFF' },
      }, { timeout: 15000 });
      setGpsEnabled(newValue);
      setMessage({ type: 'success', text: `GPS ${newValue ? 'enabled' : 'disabled'} on all cameras` });
    } catch (error) {
      setMessage({ type: 'error', text: `GPS toggle failed: ${error.response?.data?.detail || error.message}` });
    }
    setTimeout(() => setMessage(null), 5000);
  };

  // Auto-select first camera (single mode)
  useEffect(() => {
    if (cameras.length > 0 && !selectedCamera) {
      setSelectedCamera(cameras[0].serial);
    }
  }, [cameras, selectedCamera]);

  // Fullscreen listener
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  // Subscribe to WebSocket messages from App.js
  useEffect(() => {
    if (!subscribeWsMessages) return;
    const unsubscribe = subscribeWsMessages((data) => {
      switch (data.type) {
        case 'cohn_provisioning_progress':
          if (data.serial === provisioning) {
            setProvisionStep(`Step ${data.step}/${data.total}: ${data.message}`);
          }
          break;
        case 'cohn_provisioning_complete':
          setMessage({ type: 'success', text: `Camera ${data.serial} provisioned successfully!` });
          setTimeout(() => setMessage(null), 5000);
          if (data.serial === provisioning) {
            setProvisioning(null);
            setProvisionStep('');
          }
          if (onCohnUpdate) onCohnUpdate();
          break;
        case 'cohn_provisioning_error':
          if (data.serial === provisioning) {
            setProvisioning(null);
            setProvisionStep('');
          }
          setMessage({ type: 'error', text: `Provisioning failed for ${data.serial}: ${data.error}` });
          setTimeout(() => setMessage(null), 8000);
          break;
        case 'cohn_preview_started':
          if (data.success && data.stream_url) {
            setCohnCameras(prev => ({
              ...prev,
              [data.serial]: { streaming: true, streamUrl: data.stream_url }
            }));
          }
          break;
        case 'cohn_preview_stopped':
          setCohnCameras(prev => ({
            ...prev,
            [data.serial]: { streaming: false, streamUrl: null }
          }));
          break;
        default:
          break;
      }
    });
    return unsubscribe;
  }, [subscribeWsMessages, provisioning, onCohnUpdate]);

  // Poll health data every 15 seconds
  const fetchHealth = useCallback(async () => {
    try {
      const response = await axios.get(`${apiUrl}/api/health/dashboard`);
      setHealthData(response.data.cameras || {});
    } catch (error) {
      console.error('Failed to fetch health:', error);
    }
  }, [apiUrl]);

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 15000);
    return () => clearInterval(interval);
  }, [fetchHealth]);

  // Check WiFi status (single mode only)
  useEffect(() => {
    if (mode !== 'single') return;
    const checkWifi = async () => {
      try {
        const response = await axios.get(`${apiUrl}/api/wifi/current`);
        setCurrentWifi(response.data.display_name || response.data.ssid);
        if (selectedCamera) {
          setWifiConnected(response.data.on_gopro || false);
        }
      } catch (error) {
        console.error('Failed to check WiFi:', error);
      }
    };
    checkWifi();
    const interval = setInterval(checkWifi, 2000);
    return () => clearInterval(interval);
  }, [apiUrl, selectedCamera, cameras, mode]);

  // Single camera HLS stream effect
  useEffect(() => {
    if (mode !== 'single') return;
    if (streamUrl && wifiConnected && singleVideoRef.current) {
      const startStream = async () => {
        try {
          await axios.post(`${apiUrl}/api/preview/stream-start`, null, { timeout: 15000 });
        } catch (err) {
          console.warn('Stream start request failed:', err.message);
        }
      };
      startStream();

      if (Hls.isSupported()) {
        if (singleHlsRef.current) singleHlsRef.current.destroy();
        const hls = new Hls({ enableWorker: true, lowLatencyMode: true, backBufferLength: 90 });
        hls.loadSource(streamUrl);
        hls.attachMedia(singleVideoRef.current);
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          singleVideoRef.current.play().catch(() => {});
        });
        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal) {
            if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
            else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
            else hls.destroy();
          }
        });
        singleHlsRef.current = hls;
      } else if (singleVideoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
        singleVideoRef.current.src = streamUrl;
        singleVideoRef.current.addEventListener('loadedmetadata', () => {
          singleVideoRef.current.play();
        });
      }
    }
    return () => {
      if (singleHlsRef.current) {
        singleHlsRef.current.destroy();
        singleHlsRef.current = null;
      }
    };
  }, [streamUrl, wifiConnected, apiUrl, mode]);

  // COHN multi-camera HLS effect: attach/detach HLS for each streaming camera
  useEffect(() => {
    if (mode !== 'cohn') return;

    Object.entries(cohnCameras).forEach(([serial, cam]) => {
      const videoEl = videoRefsMap.current[serial];
      if (cam.streaming && cam.streamUrl && videoEl) {
        // Already has an HLS instance for this URL - skip
        if (hlsInstancesMap.current[serial]) return;

        if (Hls.isSupported()) {
          const hls = new Hls({ enableWorker: true, lowLatencyMode: true, backBufferLength: 90 });
          hls.loadSource(cam.streamUrl);
          hls.attachMedia(videoEl);
          hls.on(Hls.Events.MANIFEST_PARSED, () => {
            videoEl.play().catch(() => {});
          });
          hls.on(Hls.Events.ERROR, (_event, data) => {
            if (data.fatal) {
              if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
              else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
              else hls.destroy();
            }
          });
          hlsInstancesMap.current[serial] = hls;
        } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
          videoEl.src = cam.streamUrl;
          videoEl.addEventListener('loadedmetadata', () => videoEl.play());
        }
      } else if (!cam.streaming && hlsInstancesMap.current[serial]) {
        hlsInstancesMap.current[serial].destroy();
        delete hlsInstancesMap.current[serial];
      }
    });

    // Cleanup on unmount
    return () => {
      Object.values(hlsInstancesMap.current).forEach(hls => {
        try { hls.destroy(); } catch (e) { /* ignore */ }
      });
      hlsInstancesMap.current = {};
    };
  }, [cohnCameras, mode]);

  // === COHN Actions ===

  const provisionCamera = async (serial) => {
    if (!wifiSSID || !wifiPassword) {
      setMessage({ type: 'error', text: 'Enter WiFi SSID and password first.' });
      setTimeout(() => setMessage(null), 5000);
      return;
    }
    setProvisioning(serial);
    setProvisionStep('Starting...');
    try {
      await axios.post(`${apiUrl}/api/cohn/provision/${serial}`, {
        wifi_ssid: wifiSSID,
        wifi_password: wifiPassword
      }, { timeout: 320000 });
      // Success handled by WS callback
    } catch (error) {
      setProvisioning(null);
      setProvisionStep('');
      setMessage({
        type: 'error',
        text: `Provisioning failed: ${error.response?.data?.detail || error.message}`
      });
      setTimeout(() => setMessage(null), 8000);
    }
  };

  const startPreview = async (serial) => {
    setMessage({ type: 'info', text: `Starting COHN preview for ${serial}...` });
    try {
      const response = await axios.post(`${apiUrl}/api/cohn/preview/start/${serial}`, null, { timeout: 20000 });
      if (response.data.success) {
        setCohnCameras(prev => ({
          ...prev,
          [serial]: { streaming: true, streamUrl: response.data.stream_url }
        }));
        setMessage({ type: 'success', text: `Preview started for ${serial}` });
      } else {
        setMessage({ type: 'error', text: `Failed: ${response.data.error || 'Unknown error'}` });
      }
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({ type: 'error', text: `Preview failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const stopPreview = async (serial) => {
    try {
      await axios.post(`${apiUrl}/api/cohn/preview/stop/${serial}`, null, { timeout: 15000 });
      // Destroy HLS instance
      if (hlsInstancesMap.current[serial]) {
        hlsInstancesMap.current[serial].destroy();
        delete hlsInstancesMap.current[serial];
      }
      setCohnCameras(prev => ({
        ...prev,
        [serial]: { streaming: false, streamUrl: null }
      }));
    } catch (error) {
      console.error('Stop COHN preview failed:', error);
    }
  };

  const startAllPreviews = async () => {
    setMessage({ type: 'info', text: 'Starting all COHN previews...' });
    try {
      const response = await axios.post(`${apiUrl}/api/cohn/preview/start`, null, { timeout: 30000 });
      const results = response.data.results || {};
      const newCohnCameras = {};
      Object.entries(results).forEach(([serial, result]) => {
        if (result.success) {
          newCohnCameras[serial] = { streaming: true, streamUrl: result.stream_url };
        }
      });
      setCohnCameras(prev => ({ ...prev, ...newCohnCameras }));
      const count = Object.values(results).filter(r => r.success).length;
      setMessage({ type: 'success', text: `Started ${count} preview(s)` });
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({ type: 'error', text: `Start all failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const stopAllPreviews = async () => {
    try {
      await axios.post(`${apiUrl}/api/cohn/preview/stop`, null, { timeout: 15000 });
      // Destroy all HLS instances
      Object.keys(hlsInstancesMap.current).forEach(serial => {
        hlsInstancesMap.current[serial].destroy();
        delete hlsInstancesMap.current[serial];
      });
      setCohnCameras({});
      setMessage({ type: 'success', text: 'All previews stopped' });
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      console.error('Stop all COHN previews failed:', error);
    }
  };

  const refreshCohnStatus = async () => {
    if (onCohnUpdate) onCohnUpdate();
  };

  // === Single Camera Actions ===

  const handleStartPreviewFor = async (serial) => {
    const camera = cameras.find(c => c.serial === serial);
    if (!camera) return;

    if (!camera.connected) {
      setMessage({ type: 'error', text: `${camera.name || serial} must be connected via BLE first.` });
      setTimeout(() => setMessage(null), 5000);
      return;
    }

    if (previewing && selectedCamera !== serial) {
      try {
        await axios.post(`${apiUrl}/api/preview/stop/${selectedCamera}`);
        if (singleHlsRef.current) {
          singleHlsRef.current.destroy();
          singleHlsRef.current = null;
        }
      } catch (err) {
        console.warn('Stop previous preview failed:', err.message);
      }
    }

    setSelectedCamera(serial);
    setPreviewing(true);
    setStreamUrl(null);
    setWifiConnected(false);
    setMessage({ type: 'info', text: `Starting preview for ${camera.name || serial}...` });

    try {
      const response = await axios.post(`${apiUrl}/api/preview/start/${serial}`, null, { timeout: 30000 });
      if (response.data.success) {
        setStreamUrl(response.data.stream_url);
        setMessage({
          type: 'success',
          text: `Preview started! Connect to ${response.data.wifi_ssid} WiFi to view stream.`
        });
      } else {
        setMessage({ type: 'error', text: 'Failed to start preview' });
        setPreviewing(false);
      }
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({
        type: 'error',
        text: `Preview failed: ${error.response?.data?.detail || error.message}`
      });
      setPreviewing(false);
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleStopPreview = async () => {
    try {
      if (selectedCamera) {
        await axios.post(`${apiUrl}/api/preview/stop/${selectedCamera}`);
      }
      if (singleHlsRef.current) {
        singleHlsRef.current.destroy();
        singleHlsRef.current = null;
      }
      setPreviewing(false);
      setStreamUrl(null);
      setMessage({ type: 'success', text: 'Preview stopped' });
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: 'Stop preview failed' });
      setTimeout(() => setMessage(null), 3000);
    }
  };

  const handleConnectWifi = async () => {
    const camera = cameras.find(c => c.serial === selectedCamera);
    if (!camera) return;

    setMessage({ type: 'info', text: `Enabling WiFi on ${camera.name || `GoPro ${camera.serial}`}...` });

    try {
      if (camera.connected) {
        await axios.post(`${apiUrl}/api/wifi/enable-all`, null, { timeout: 30000 });
        setMessage({ type: 'info', text: `WiFi AP enabled, waiting for broadcast...` });
        await new Promise(resolve => setTimeout(resolve, 5000));
      }
      setMessage({ type: 'info', text: `Connecting to ${camera.wifi_ssid}...` });
      const response = await axios.post(`${apiUrl}/api/wifi/connect-camera/${camera.serial}`, null, { timeout: 60000 });
      if (response.data.success) {
        setMessage({ type: 'success', text: `Connected to ${camera.wifi_ssid}!` });
        setWifiConnected(true);
      } else {
        setMessage({ type: 'error', text: 'WiFi connection failed.' });
      }
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({ type: 'error', text: `WiFi connection failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  // === Helpers ===

  const handleFullscreenVideo = useCallback((videoElement) => {
    if (videoElement && videoElement.requestFullscreen) {
      videoElement.requestFullscreen();
    }
  }, []);

  const handleFullscreenGrid = useCallback(() => {
    if (gridRef.current) {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        gridRef.current.requestFullscreen();
      }
    }
  }, []);

  const formatStorage = (kb) => {
    if (kb == null) return '--';
    if (kb >= 1048576) return `${(kb / 1048576).toFixed(1)} GB`;
    if (kb >= 1024) return `${(kb / 1024).toFixed(0)} MB`;
    return `${kb} KB`;
  };

  const formatTime = (seconds) => {
    if (seconds == null) return '--:--:--';
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  const layoutConfig = LAYOUTS.find(l => l.id === activeLayout) || LAYOUTS[0];

  // Sort cameras: streaming > provisioned+online > provisioned > connected > disconnected
  const sortedCameras = [...cameras].sort((a, b) => {
    const aStreaming = mode === 'cohn' ? (cohnCameras[a.serial]?.streaming || false) : (previewing && a.serial === selectedCamera);
    const bStreaming = mode === 'cohn' ? (cohnCameras[b.serial]?.streaming || false) : (previewing && b.serial === selectedCamera);
    if (aStreaming && !bStreaming) return -1;
    if (!aStreaming && bStreaming) return 1;
    if (mode === 'cohn') {
      const aCohn = cohnStatus?.[a.serial];
      const bCohn = cohnStatus?.[b.serial];
      const aOnline = aCohn?.provisioned && aCohn?.online;
      const bOnline = bCohn?.provisioned && bCohn?.online;
      if (aOnline && !bOnline) return -1;
      if (!aOnline && bOnline) return 1;
      const aProv = aCohn?.provisioned || false;
      const bProv = bCohn?.provisioned || false;
      if (aProv && !bProv) return -1;
      if (!aProv && bProv) return 1;
    }
    if (a.connected && !b.connected) return -1;
    if (!a.connected && b.connected) return 1;
    return 0;
  });

  const provisionedCount = Object.values(cohnStatus || {}).filter(c => c.provisioned).length;
  const onlineCount = Object.values(cohnStatus || {}).filter(c => c.online).length;
  const unprovisionedCount = cameras.length - provisionedCount;
  const streamingCount = Object.values(cohnCameras).filter(c => c.streaming).length;

  // === Render ===

  const renderInfoCard = (camera, onPreviewClick) => {
    const h = healthData[camera.serial] || {};
    const batteryPct = h.battery_percent ?? camera.battery_level;
    const batteryColor = batteryPct == null ? '#555' : batteryPct > 50 ? '#10b981' : batteryPct > 20 ? '#f59e0b' : '#ef4444';
    const thermalState = h.system_hot ? 'hot' : h.thermal_mitigation ? 'warm' : 'normal';
    const cohn = cohnStatus?.[camera.serial];

    return (
      <div className="camera-info-card">
        <div className="info-card-header">
          <div className="info-card-name">
            <span className="cam-name">{camera.name || `GoPro ${camera.serial}`}</span>
            <span className="cam-serial">{camera.serial}</span>
          </div>
          <div className="info-card-dots">
            {camera.recording && <span className="dot-indicator dot-recording" title="Recording"></span>}
            {mode === 'cohn' && cohn?.provisioned && (
              <span className={`dot-indicator ${cohn.online ? 'dot-conn-on' : 'dot-conn-off'}`} title={cohn.online ? 'COHN Online' : 'COHN Offline'}></span>
            )}
            <span className={`dot-indicator dot-conn-${camera.connected ? 'on' : 'off'}`} title={camera.connected ? 'BLE Connected' : 'BLE Disconnected'}></span>
          </div>
        </div>

        {camera.connected || (mode === 'cohn' && cohn?.provisioned) ? (
          <div className="info-card-body">
            {/* Battery */}
            <div className="info-row">
              <span className="info-label">Battery</span>
              <div className="info-bar-track">
                <div className="info-bar-fill" style={{ width: `${batteryPct || 0}%`, background: batteryColor }}></div>
              </div>
              <span className="info-val" style={{ color: batteryColor }}>
                {batteryPct != null ? `${batteryPct}%` : '--'}
              </span>
            </div>

            {/* Storage */}
            <div className="info-row">
              <span className="info-label">Storage</span>
              <span className="info-val">{formatStorage(h.storage_remaining_kb)}</span>
              {h.video_remaining_min != null && (
                <span className="info-sub">{h.video_remaining_min} min left</span>
              )}
            </div>

            {/* Recording Duration */}
            {camera.recording && (
              <div className="info-row info-row-rec">
                <span className="info-label">REC</span>
                <span className="info-val info-val-rec">{formatTime(h.recording_duration_sec)}</span>
              </div>
            )}

            {/* Status Row */}
            <div className="info-status-row">
              <span className={`info-chip thermal-${thermalState}`}>
                {thermalState === 'hot' ? 'Overheating' : thermalState === 'warm' ? 'Warm' : 'Temp OK'}
              </span>
              <span className={`info-chip gps-${h.gps_lock ? 'on' : 'off'}`}>
                {h.gps_lock ? 'GPS Lock' : 'No GPS'}
              </span>
              {mode === 'cohn' && cohn?.provisioned && (
                <span className={`status-badge ${cohn.online ? 'provisioned' : 'not-provisioned'}`}>
                  {cohn.online ? 'COHN Online' : 'COHN Offline'}
                </span>
              )}
            </div>

            {/* COHN IP Badge */}
            {mode === 'cohn' && cohn?.ip_address && (
              <div className="info-row">
                <span className="info-label">IP</span>
                <span className="ip-badge">{cohn.ip_address}</span>
              </div>
            )}

            {/* Media Counts */}
            {(h.num_videos != null || h.num_photos != null) && (
              <div className="info-media-row">
                {h.num_videos != null && <span className="info-media">{h.num_videos} videos</span>}
                {h.num_photos != null && <span className="info-media">{h.num_photos} photos</span>}
              </div>
            )}

            {/* Per-Camera Actions */}
            {mode === 'cohn' && cohn?.provisioned && cohn?.online && (
              <div className="per-cam-actions">
                <button
                  className="btn btn-secondary btn-xs"
                  onClick={() => handleApplySettings([camera.serial])}
                  disabled={applyingSettings}
                  title="Apply current settings to this camera"
                >
                  Apply Settings
                </button>
              </div>
            )}

            {/* Preview Button */}
            <button
              className="btn btn-preview"
              onClick={() => onPreviewClick(camera.serial)}
              disabled={provisioning === camera.serial}
            >
              {mode === 'cohn'
                ? (cohn?.provisioned ? 'Start COHN Preview' : 'Not Provisioned')
                : (previewing && selectedCamera === camera.serial ? 'Previewing...' : 'Start Preview')
              }
            </button>
          </div>
        ) : (
          <div className="info-card-disconnected">
            <span className="disconnected-text">Disconnected</span>
            <p className="disconnected-hint">
              {mode === 'cohn' ? 'Provision via COHN or connect BLE' : 'Connect via Camera Management tab'}
            </p>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="live-preview">
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      {/* Mode Toggle */}
      <div className="mode-toggle">
        <button
          className={`mode-btn ${mode === 'cohn' ? 'active' : ''}`}
          onClick={() => setMode('cohn')}
        >
          Multi-Camera (COHN)
        </button>
        <button
          className={`mode-btn ${mode === 'single' ? 'active' : ''}`}
          onClick={() => setMode('single')}
        >
          Single Camera (WiFi AP)
        </button>
      </div>

      {/* COHN Mode */}
      {mode === 'cohn' && (
        <div className="card">
          <div className="card-header">
            <h2>Multi-Camera COHN Preview</h2>
            <div className="header-actions">
              <button className="btn btn-secondary btn-sm" onClick={refreshCohnStatus}>
                Refresh Status
              </button>
              <button
                className="btn btn-secondary btn-sm fullscreen-grid-btn"
                onClick={handleFullscreenGrid}
                title={isFullscreen ? 'Exit Fullscreen' : 'Fullscreen Grid'}
              >
                {isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
              </button>
            </div>
          </div>

          {/* COHN Setup Section */}
          <div className="cohn-setup">
            <div className="wifi-input-group">
              <h3>COHN Provisioning</h3>
              <p className="cohn-setup-hint">
                Provision cameras to join your home WiFi. Once provisioned, all cameras can stream simultaneously.
              </p>
              <div className="input-row">
                <input
                  type="text"
                  placeholder="Home WiFi SSID"
                  value={wifiSSID}
                  onChange={(e) => setWifiSSID(e.target.value)}
                  className="wifi-input"
                />
                <input
                  type="password"
                  placeholder="WiFi Password"
                  value={wifiPassword}
                  onChange={(e) => setWifiPassword(e.target.value)}
                  className="wifi-input"
                />
              </div>
            </div>

            <div className="provision-list">
              {sortedCameras.map(camera => {
                const cohn = cohnStatus?.[camera.serial];
                const isProvisioning = provisioning === camera.serial;

                return (
                  <div key={camera.serial} className="provision-row">
                    <div className="provision-info">
                      <span className="provision-name">{camera.name || `GoPro ${camera.serial}`}</span>
                      {cohn?.provisioned ? (
                        <span className="status-badge provisioned">
                          Provisioned {cohn.ip_address ? `(${cohn.ip_address})` : ''}
                        </span>
                      ) : isProvisioning ? (
                        <span className="status-badge provisioning">Provisioning...</span>
                      ) : (
                        <span className="status-badge not-provisioned">Not Provisioned</span>
                      )}
                    </div>
                    <div className="provision-actions">
                      {isProvisioning ? (
                        <div className="provision-progress">
                          <span className="progress-spinner"></span>
                          <span className="progress-text">{provisionStep}</span>
                        </div>
                      ) : cohn?.provisioned ? (
                        <span className={`dot-indicator ${cohn.online ? 'dot-conn-on' : 'dot-conn-off'}`}
                          title={cohn.online ? 'Online' : 'Offline'}></span>
                      ) : (
                        <button
                          className="btn btn-primary btn-sm"
                          onClick={() => provisionCamera(camera.serial)}
                          disabled={!wifiSSID || !wifiPassword || provisioning !== null}
                        >
                          Provision
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Multi-Camera Controls */}
          {provisionedCount > 0 && (
            <div className="multi-controls">
              <div className="controls-left">
                <span className="controls-label">
                  {provisionedCount} provisioned, {onlineCount} online, {streamingCount} streaming
                </span>
              </div>
              <div className="controls-right">
                <button className="btn btn-success btn-sm" onClick={startAllPreviews} disabled={onlineCount === 0}>
                  Start All Previews
                </button>
                <button className="btn btn-danger btn-sm" onClick={stopAllPreviews} disabled={streamingCount === 0}>
                  Stop All
                </button>
              </div>
            </div>
          )}

          {/* Camera Settings Panel */}
          {provisionedCount > 0 && (
            <div className="settings-panel">
              <button
                className="settings-toggle"
                onClick={() => setSettingsOpen(!settingsOpen)}
              >
                <span className="settings-toggle-label">Camera Settings</span>
                <span className={`settings-toggle-arrow ${settingsOpen ? 'open' : ''}`}>&#9662;</span>
              </button>

              {settingsOpen && (
                <div className="settings-body">
                  {/* Settings Grid */}
                  <div className="settings-grid">
                    {Object.entries(SETTINGS_OPTIONS).map(([key, options]) => (
                      <div key={key} className="setting-field">
                        <label className="setting-label">{SETTINGS_LABELS[key]}</label>
                        <select
                          className="setting-select"
                          value={settingsValues[key]}
                          onChange={(e) => setSettingsValues(prev => ({ ...prev, [key]: e.target.value }))}
                        >
                          {options.map(opt => {
                            const val = typeof opt === 'object' ? opt.value : opt;
                            const label = typeof opt === 'object' ? opt.label : opt;
                            return <option key={val} value={val}>{label}</option>;
                          })}
                        </select>
                      </div>
                    ))}

                    {/* GPS Toggle */}
                    <div className="setting-field">
                      <label className="setting-label">GPS</label>
                      <button
                        className={`gps-toggle-btn ${gpsEnabled ? 'gps-on' : 'gps-off'}`}
                        onClick={handleToggleGps}
                      >
                        {gpsEnabled ? 'ON' : 'OFF'}
                      </button>
                    </div>
                  </div>

                  <div className="settings-actions">
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={handleApplySettings}
                      disabled={applyingSettings}
                    >
                      {applyingSettings ? 'Applying...' : 'Apply to All Cameras'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Layout Selector */}
          <div className="layout-selector">
            {LAYOUTS.map((layout) => (
              <button
                key={layout.id}
                className={`layout-btn ${activeLayout === layout.id ? 'active' : ''}`}
                onClick={() => setActiveLayout(layout.id)}
                title={layout.label}
              >
                <span className="layout-icon">{layout.icon}</span>
                <span className="layout-label">{layout.label}</span>
              </button>
            ))}
          </div>

          {/* Camera Grid */}
          {cameras.length === 0 ? (
            <div className="empty-state">
              <p>No cameras added. Go to Camera Management tab to add cameras.</p>
            </div>
          ) : (
            <div ref={gridRef} className={`camera-grid ${layoutConfig.css} ${isFullscreen ? 'fullscreen-mode' : ''}`}>
              {sortedCameras.map((camera) => {
                const camState = cohnCameras[camera.serial];
                const isCohnStreaming = camState?.streaming && camState?.streamUrl;
                const cohn = cohnStatus?.[camera.serial];

                return (
                  <div
                    key={camera.serial}
                    className={`camera-panel ${isCohnStreaming ? 'cohn-streaming' : ''} ${cohn?.online ? 'cohn-online' : ''} ${!camera.connected && !cohn?.provisioned ? 'disconnected-panel' : ''} ${camera.recording ? 'recording-panel' : ''}`}
                  >
                    {isCohnStreaming ? (
                      <div className="video-wrapper">
                        <video
                          ref={(el) => { if (el) videoRefsMap.current[camera.serial] = el; }}
                          controls
                          autoPlay
                          muted
                          playsInline
                          className="video-player"
                        />
                        <div className="stream-info">
                          <div className="stream-info-left">
                            <span className="live-badge">LIVE</span>
                            {camera.recording && <span className="recording-badge">REC</span>}
                            <span className="camera-label-badge">{camera.name || `GoPro ${camera.serial}`}</span>
                            {cohn?.ip_address && <span className="ip-badge">{cohn.ip_address}</span>}
                          </div>
                          <div className="stream-info-right">
                            <button
                              className="fullscreen-btn"
                              onClick={() => handleFullscreenVideo(videoRefsMap.current[camera.serial])}
                              title="Fullscreen"
                            >
                              &#x26F6;
                            </button>
                            <button
                              className="fullscreen-btn"
                              onClick={() => stopPreview(camera.serial)}
                              title="Stop"
                              style={{ marginLeft: '0.25rem' }}
                            >
                              &#x23F9;
                            </button>
                          </div>
                        </div>
                      </div>
                    ) : (
                      renderInfoCard(camera, (serial) => {
                        const c = cohnStatus?.[serial];
                        if (c?.provisioned) {
                          startPreview(serial);
                        } else {
                          setMessage({ type: 'error', text: 'Camera must be provisioned for COHN first.' });
                          setTimeout(() => setMessage(null), 5000);
                        }
                      })
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Single Camera (WiFi AP) Mode */}
      {mode === 'single' && (
        <div className="card">
          <div className="card-header">
            <h2>Live Preview</h2>
            <div className="header-actions">
              {previewing && (
                <button className="btn btn-danger btn-sm" onClick={handleStopPreview}>
                  Stop Preview
                </button>
              )}
              <button
                className="btn btn-secondary btn-sm fullscreen-grid-btn"
                onClick={handleFullscreenGrid}
                title={isFullscreen ? 'Exit Fullscreen' : 'Fullscreen Grid'}
              >
                {isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
              </button>
            </div>
          </div>

          {/* Layout Selector Bar */}
          <div className="layout-selector">
            {LAYOUTS.map((layout) => (
              <button
                key={layout.id}
                className={`layout-btn ${activeLayout === layout.id ? 'active' : ''}`}
                onClick={() => setActiveLayout(layout.id)}
                title={layout.label}
              >
                <span className="layout-icon">{layout.icon}</span>
                <span className="layout-label">{layout.label}</span>
              </button>
            ))}
            <div className="layout-spacer"></div>
            {previewing && (
              <div className={`wifi-indicator ${wifiConnected ? 'connected' : 'disconnected'}`}>
                <span className="wifi-dot"></span>
                {wifiConnected ? `WiFi: ${currentWifi || 'Connected'}` : 'WiFi: Not Connected'}
                {!wifiConnected && (
                  <button className="btn btn-primary btn-xs" onClick={handleConnectWifi}>
                    Connect
                  </button>
                )}
              </div>
            )}
          </div>

          {cameras.length === 0 ? (
            <div className="empty-state">
              <p>No cameras added. Go to Camera Management tab to add cameras.</p>
            </div>
          ) : (
            <div ref={gridRef} className={`camera-grid ${layoutConfig.css} ${isFullscreen ? 'fullscreen-mode' : ''}`}>
              {sortedCameras.map((camera) => {
                const isActive = previewing && camera.serial === selectedCamera;
                const isStreaming = isActive && wifiConnected && streamUrl;
                const isWaiting = isActive && !wifiConnected;

                return (
                  <div
                    key={camera.serial}
                    className={`camera-panel ${isActive ? 'active-panel' : ''} ${!camera.connected ? 'disconnected-panel' : ''} ${camera.recording ? 'recording-panel' : ''}`}
                  >
                    {isStreaming && (
                      <div className="video-wrapper">
                        <video
                          ref={singleVideoRef}
                          controls
                          autoPlay
                          muted
                          playsInline
                          className="video-player"
                        />
                        <div className="stream-info">
                          <div className="stream-info-left">
                            <span className="live-badge">LIVE</span>
                            {camera.recording && <span className="recording-badge">REC</span>}
                            <span className="camera-label-badge">{camera.name || `GoPro ${camera.serial}`}</span>
                          </div>
                          <div className="stream-info-right">
                            <button
                              className="fullscreen-btn"
                              onClick={() => handleFullscreenVideo(singleVideoRef.current)}
                              title="Fullscreen"
                            >
                              &#x26F6;
                            </button>
                          </div>
                        </div>
                      </div>
                    )}

                    {isWaiting && (
                      <div className="video-placeholder waiting-placeholder">
                        <div className="placeholder-icon">&#x1F4F9;</div>
                        <h3>Waiting for WiFi...</h3>
                        <p>Connect to <strong>{camera.wifi_ssid}</strong></p>
                        <button className="btn btn-primary btn-sm" onClick={handleConnectWifi} style={{ marginTop: '0.75rem' }}>
                          Connect WiFi
                        </button>
                      </div>
                    )}

                    {!isActive && renderInfoCard(camera, handleStartPreviewFor)}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default LivePreview;
