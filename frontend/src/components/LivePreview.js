import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import Hls from 'hls.js';
import './LivePreview.css';

function LivePreview({ cameras, apiUrl }) {
  const [selectedCamera, setSelectedCamera] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [streamUrl, setStreamUrl] = useState(null);
  const [message, setMessage] = useState(null);
  const [currentWifi, setCurrentWifi] = useState(null);
  const [wifiConnected, setWifiConnected] = useState(false);
  const videoRef = useRef(null);
  const hlsRef = useRef(null);

  // Auto-select first camera
  useEffect(() => {
    if (cameras.length > 0 && !selectedCamera) {
      setSelectedCamera(cameras[0].serial);
    }
  }, [cameras, selectedCamera]);

  // Check WiFi status periodically
  useEffect(() => {
    const checkWifi = async () => {
      try {
        const response = await axios.get(`${apiUrl}/api/wifi/current`);
        // Use display_name which works on macOS 26+ (where SSID is hidden)
        setCurrentWifi(response.data.display_name || response.data.ssid);

        // Check if connected to GoPro WiFi (IP-based detection for macOS 26+)
        if (selectedCamera) {
          if (response.data.on_gopro) {
            setWifiConnected(true);
          } else {
            setWifiConnected(false);
          }
        }
      } catch (error) {
        console.error('Failed to check WiFi:', error);
      }
    };

    checkWifi();
    const interval = setInterval(checkWifi, 2000); // Check every 2 seconds

    return () => clearInterval(interval);
  }, [apiUrl, selectedCamera, cameras]);

  // Start the camera stream and initialize HLS player when WiFi is connected
  useEffect(() => {
    if (streamUrl && wifiConnected && videoRef.current) {
      // Must call stream/start on the camera over HTTP before HLS will work
      // Route through backend to avoid CORS issues
      const startStream = async () => {
        try {
          console.log('Starting camera stream via backend proxy...');
          await axios.post(`${apiUrl}/api/preview/stream-start`, null, { timeout: 15000 });
          console.log('Camera stream started successfully');
        } catch (err) {
          console.warn('Stream start request failed (may already be running):', err.message);
        }
      };
      startStream();

      if (Hls.isSupported()) {
        // Cleanup previous instance
        if (hlsRef.current) {
          hlsRef.current.destroy();
        }

        const hls = new Hls({
          enableWorker: true,
          lowLatencyMode: true,
          backBufferLength: 90
        });

        hls.loadSource(streamUrl);
        hls.attachMedia(videoRef.current);

        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          console.log('HLS manifest parsed, starting playback...');
          videoRef.current.play().catch(err => {
            console.error('Playback failed:', err);
            setMessage({ type: 'error', text: 'Playback failed. Click on video to play.' });
          });
        });

        hls.on(Hls.Events.ERROR, (_event, data) => {
          console.error('HLS error:', data);
          if (data.fatal) {
            switch (data.type) {
              case Hls.ErrorTypes.NETWORK_ERROR:
                setMessage({
                  type: 'error',
                  text: 'Network error. Make sure you are connected to camera WiFi.'
                });
                hls.startLoad();
                break;
              case Hls.ErrorTypes.MEDIA_ERROR:
                hls.recoverMediaError();
                break;
              default:
                hls.destroy();
                setMessage({ type: 'error', text: 'Fatal error occurred. Try restarting preview.' });
                break;
            }
          }
        });

        hlsRef.current = hls;
      } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
        // For Safari (native HLS support)
        videoRef.current.src = streamUrl;
        videoRef.current.addEventListener('loadedmetadata', () => {
          videoRef.current.play();
        });
      }
    }

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
      }
    };
  }, [streamUrl, wifiConnected, apiUrl]);

  const handleStartPreview = async () => {
    if (!selectedCamera) {
      setMessage({ type: 'error', text: 'Please select a camera first' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    const camera = cameras.find(c => c.serial === selectedCamera);
    if (!camera.connected) {
      setMessage({ type: 'error', text: 'Camera must be connected via BLE first. Go to Camera Management tab.' });
      setTimeout(() => setMessage(null), 5000);
      return;
    }

    setPreviewing(true);
    setMessage({ type: 'info', text: 'Starting live preview...' });

    try {
      const response = await axios.post(
        `${apiUrl}/api/preview/start/${selectedCamera}`,
        null,
        { timeout: 30000 }
      );

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
      console.error('Preview error:', error);
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
      await axios.post(`${apiUrl}/api/preview/stop/${selectedCamera}`);

      // Cleanup
      if (hlsRef.current) {
        hlsRef.current.destroy();
        hlsRef.current = null;
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

  const handleCameraChange = (e) => {
    const newSerial = e.target.value;

    // Stop current preview if active
    if (previewing) {
      handleStopPreview();
    }

    setSelectedCamera(newSerial);
    setStreamUrl(null);
    setWifiConnected(false);
  };

  const handleConnectWifi = async () => {
    const camera = cameras.find(c => c.serial === selectedCamera);
    if (!camera) return;

    // Step 1: Enable WiFi AP on camera via BLE
    setMessage({ type: 'info', text: `Enabling WiFi on ${camera.name || `GoPro ${camera.serial}`}...` });

    try {
      // Enable WiFi AP via BLE first (camera must broadcast before Mac can connect)
      if (camera.connected) {
        await axios.post(`${apiUrl}/api/wifi/enable-all`, null, { timeout: 30000 });
        // Wait for WiFi AP to start broadcasting
        setMessage({ type: 'info', text: `WiFi AP enabled, waiting for broadcast...` });
        await new Promise(resolve => setTimeout(resolve, 5000));
      }

      // Step 2: Connect Mac to camera WiFi using server-side credentials
      setMessage({ type: 'info', text: `Connecting to ${camera.wifi_ssid}...` });

      const response = await axios.post(
        `${apiUrl}/api/wifi/connect-camera/${camera.serial}`,
        null,
        { timeout: 60000 }
      );

      if (response.data.success) {
        setMessage({ type: 'success', text: `Connected to ${camera.wifi_ssid}!` });
        setWifiConnected(true);
      } else {
        setMessage({ type: 'error', text: 'WiFi connection failed. Try again or connect manually via macOS WiFi settings.' });
      }
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({ type: 'error', text: `WiFi connection failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const selectedCameraObj = cameras.find(c => c.serial === selectedCamera);

  return (
    <div className="live-preview">
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h2>Live Preview - Single Camera</h2>
        </div>

        {cameras.length === 0 ? (
          <div className="empty-state">
            <p>No cameras added. Go to Camera Management tab to add cameras.</p>
          </div>
        ) : (
          <>
            {/* Camera Selection */}
            <div className="preview-controls">
              <div className="control-group">
                <label htmlFor="camera-select">Select Camera:</label>
                <select
                  id="camera-select"
                  value={selectedCamera || ''}
                  onChange={handleCameraChange}
                  className="camera-select"
                >
                  {cameras.map((camera) => {
                    const statusIcon = camera.connected ? '✅' : '⚪';
                    const statusText = camera.connected ? 'Connected' : 'Disconnected';
                    const recordingIcon = camera.recording ? '🔴' : '';
                    return (
                      <option key={camera.serial} value={camera.serial}>
                        {statusIcon} {camera.name || `GoPro ${camera.serial}`} - {statusText} {recordingIcon}
                      </option>
                    );
                  })}
                </select>
              </div>

              <div className="button-group">
                <button
                  className="btn btn-success"
                  onClick={handleStartPreview}
                  disabled={!selectedCamera || previewing}
                >
                  {previewing ? '📹 Previewing...' : '▶️ Start Preview'}
                </button>
                <button
                  className="btn btn-danger"
                  onClick={handleStopPreview}
                  disabled={!previewing}
                >
                  ⏹️ Stop Preview
                </button>
              </div>
            </div>

            {/* WiFi Status and Connection */}
            {previewing && selectedCameraObj && (
              <div className="wifi-status-section">
                <div className={`wifi-status ${wifiConnected ? 'connected' : 'disconnected'}`}>
                  <div className="status-header">
                    <h3>
                      {wifiConnected ? '✅ WiFi Connected' : '⚠️ WiFi Not Connected'}
                    </h3>
                    <div className="current-wifi">
                      Current WiFi: <strong>{currentWifi || 'None'}</strong>
                    </div>
                  </div>

                  {!wifiConnected && (
                    <div className="wifi-instructions">
                      <p>
                        To view the live stream, connect to camera WiFi:
                      </p>
                      <div className="wifi-details">
                        <div className="wifi-detail-item">
                          <strong>SSID:</strong> {selectedCameraObj.wifi_ssid}
                        </div>
                        <div className="wifi-detail-item">
                          <strong>Password:</strong> (stored on server)
                        </div>
                      </div>
                      <button
                        className="btn btn-primary"
                        onClick={handleConnectWifi}
                      >
                        📡 Connect to Camera WiFi
                      </button>
                      <p className="manual-instruction">
                        Or manually connect through your system WiFi settings
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Video Player */}
            {previewing && (
              <div className="video-container">
                {wifiConnected ? (
                  <div className="video-wrapper">
                    <video
                      ref={videoRef}
                      controls
                      autoPlay
                      muted
                      playsInline
                      className="video-player"
                    />
                    <div className="stream-info">
                      <span className="live-badge">🔴 LIVE</span>
                      {selectedCameraObj?.recording && (
                        <span className="recording-badge">⏺ RECORDING</span>
                      )}
                      <span className="stream-url-info">Stream: {streamUrl}</span>
                    </div>
                  </div>
                ) : (
                  <div className="video-placeholder">
                    <div className="placeholder-icon">📹</div>
                    <h3>Waiting for WiFi Connection...</h3>
                    <p>Connect to <strong>{selectedCameraObj?.wifi_ssid}</strong> to view live stream</p>
                  </div>
                )}
              </div>
            )}

            {/* Instructions */}
            {!previewing && (
              <div className="instructions-section">
                <h3>How to Use Live Preview</h3>
                <ol>
                  <li><strong>Select a camera</strong> from the dropdown above</li>
                  <li><strong>Click "Start Preview"</strong> to activate preview mode (via BLE)</li>
                  <li><strong>Connect to camera WiFi</strong> using the button or system settings</li>
                  <li><strong>Video will auto-play</strong> once WiFi connection is established</li>
                  <li><strong>Click "Stop Preview"</strong> when done to save battery</li>
                </ol>

                <div className="alert alert-info">
                  <strong>💡 Note:</strong> Each GoPro has its own WiFi network. You can only view one camera at a time from a single device. For multi-camera monitoring, use multiple devices (phones, tablets, laptops).
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default LivePreview;
