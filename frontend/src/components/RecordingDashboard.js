import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import './RecordingDashboard.css';

function RecordingDashboard({ cameras, onCamerasUpdate, apiUrl, activeShoot, onShootUpdate }) {
  const [recordingTime, setRecordingTime] = useState(0);
  const [message, setMessage] = useState(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [recordingStartedAt, setRecordingStartedAt] = useState(null);
  const [healthData, setHealthData] = useState({});

  // Shoot management state
  const [shoots, setShoots] = useState([]);
  const [newShootName, setNewShootName] = useState('');
  const [creatingShoot, setCreatingShoot] = useState(false);

  const connectedCameras = cameras.filter(cam => cam.connected);
  const recordingCameras = cameras.filter(cam => cam.recording);
  const isRecording = recordingCameras.length > 0;

  // Timer for recording duration
  useEffect(() => {
    let interval;
    if (isRecording) {
      if (!recordingStartedAt) {
        setRecordingStartedAt(Date.now());
      }
      interval = setInterval(() => {
        setRecordingTime(prev => prev + 1);
      }, 1000);
    } else {
      setRecordingTime(0);
      setRecordingStartedAt(null);
    }
    return () => clearInterval(interval);
  }, [isRecording, recordingStartedAt]);

  // Fetch shoots on mount
  useEffect(() => {
    fetchShoots();
  }, []);

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

  const fetchShoots = async () => {
    try {
      const response = await axios.get(`${apiUrl}/api/shoots`);
      setShoots(response.data.shoots);
    } catch (error) {
      console.error('Failed to fetch shoots:', error);
    }
  };

  const handleCreateShoot = async () => {
    if (!newShootName.trim()) return;
    setCreatingShoot(true);
    try {
      await axios.post(`${apiUrl}/api/shoots`, { name: newShootName.trim() });
      setNewShootName('');
      onShootUpdate();
      fetchShoots();
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to create shoot: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    } finally {
      setCreatingShoot(false);
    }
  };

  const handleSelectShoot = async (shootId) => {
    try {
      await axios.post(`${apiUrl}/api/shoots/active`, { shoot_id: shootId });
      onShootUpdate();
      fetchShoots();
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to activate shoot: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleEndShoot = async () => {
    try {
      await axios.post(`${apiUrl}/api/shoots/deactivate`);
      onShootUpdate();
      fetchShoots();
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to end shoot: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleDeleteShoot = async (shootId, shootName) => {
    if (!window.confirm(`Delete shoot "${shootName}"? This cannot be undone.`)) return;
    try {
      await axios.delete(`${apiUrl}/api/shoots/${shootId}`);
      onShootUpdate();
      fetchShoots();
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to delete shoot: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const formatTime = (seconds) => {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  const formatStorage = (kb) => {
    if (kb == null) return '--';
    if (kb >= 1048576) return `${(kb / 1048576).toFixed(1)} GB`;
    if (kb >= 1024) return `${(kb / 1024).toFixed(0)} MB`;
    return `${kb} KB`;
  };

  const handleStartRecording = async () => {
    if (connectedCameras.length === 0) {
      setMessage({ type: 'error', text: 'No cameras connected! Go to Camera Management to connect cameras.' });
      setTimeout(() => setMessage(null), 5000);
      return;
    }

    setStarting(true);
    setMessage({ type: 'info', text: `Starting recording on ${connectedCameras.length} cameras...` });

    try {
      const response = await axios.post(`${apiUrl}/api/recording/start`, null, { timeout: 120000 });
      const results = response.data.results;
      const successCount = Object.values(results).filter(r => r).length;
      const totalCount = Object.keys(results).length;

      if (successCount > 0) {
        const takeInfo = response.data.take ? ` (Take ${response.data.take.take_number})` : '';
        setMessage({ type: 'success', text: `Recording started on ${successCount}/${totalCount} cameras!${takeInfo}` });
      } else {
        setMessage({ type: 'error', text: 'Failed to start recording on any camera.' });
      }

      await onCamerasUpdate();
      onShootUpdate();
      setTimeout(() => onCamerasUpdate(), 1000);
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to start recording: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setStarting(false);
    }
  };

  const handleStopRecording = async () => {
    setStopping(true);
    setMessage({ type: 'info', text: 'Stopping recording on all cameras...' });

    try {
      const response = await axios.post(`${apiUrl}/api/recording/stop`, null, { timeout: 30000 });
      const results = response.data.results;
      const successCount = Object.values(results).filter(r => r).length;
      const totalCount = Object.keys(results).length;

      setMessage({ type: 'success', text: `Recording stopped on ${successCount}/${totalCount} cameras!` });
      await onCamerasUpdate();
      onShootUpdate();

      setTimeout(() => {
        onCamerasUpdate();
        setMessage({ type: 'success', text: 'Files saved! You can now download them.' });
        setTimeout(() => setMessage(null), 5000);
      }, 5000);
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to stop recording: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setStopping(false);
    }
  };

  // Compute alerts
  const alerts = [];
  Object.values(healthData).forEach(h => {
    if (h.system_hot) alerts.push({ type: 'danger', text: `${h.name || h.serial} is overheating!` });
    if (h.too_cold) alerts.push({ type: 'warning', text: `${h.name || h.serial} too cold to record` });
    if (h.thermal_mitigation) alerts.push({ type: 'warning', text: `${h.name || h.serial} thermal mitigation active` });
    if (h.sd_status && h.sd_status !== 'None' && h.sd_status.includes('FULL')) alerts.push({ type: 'danger', text: `${h.name || h.serial} SD card full!` });
    if (h.battery_percent != null && h.battery_percent < 15) alerts.push({ type: 'danger', text: `${h.name || h.serial} battery critical (${h.battery_percent}%)` });
  });

  // Compute totals
  const totalStorageKB = Object.values(healthData).reduce((sum, h) => sum + (h.storage_remaining_kb || 0), 0);
  const totalRecordingSec = Object.values(healthData).reduce((sum, h) => sum + (h.recording_duration_sec || 0), 0);

  const currentTakeNumber = activeShoot ? activeShoot.current_take_number : 0;
  const nextTakeNumber = currentTakeNumber + 1;

  return (
    <div className="recording-dashboard">
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      {/* Section A: Recording Controls Top Bar */}
      <div className="card dashboard-top-bar">
        <div className="top-bar-left">
          <div className={`recording-indicator ${isRecording ? 'active' : ''}`}>
            {isRecording && <span className="pulse-dot"></span>}
            <span className="status-text">
              {isRecording ? 'RECORDING' : 'READY'}
            </span>
          </div>
          {isRecording && (
            <div className="recording-timer">{formatTime(recordingTime)}</div>
          )}
        </div>

        <div className="top-bar-center">
          {connectedCameras.length === 0 ? (
            <div className="alert alert-info" style={{ margin: 0, padding: '0.5rem 1rem', fontSize: '0.85rem' }}>
              No cameras connected. Connect cameras first.
            </div>
          ) : (
            <div className="recording-controls">
              <button
                className={`btn btn-record ${isRecording ? 'recording' : ''}`}
                onClick={handleStartRecording}
                disabled={isRecording || starting}
              >
                {starting ? (
                  <><span className="spinner"></span> Starting...</>
                ) : (
                  <><span className="record-icon">&#9210;</span> {activeShoot ? `Start Take ${nextTakeNumber}` : 'Start Recording'}</>
                )}
              </button>
              <button
                className="btn btn-stop"
                onClick={handleStopRecording}
                disabled={!isRecording || stopping}
              >
                {stopping ? (
                  <><span className="spinner"></span> Stopping...</>
                ) : (
                  <><span className="stop-icon">&#9209;</span> Stop Recording</>
                )}
              </button>
            </div>
          )}
        </div>

        <div className="top-bar-right">
          <div className="cam-count-badges">
            <span className="cam-badge total">{cameras.length} Total</span>
            <span className="cam-badge connected">{connectedCameras.length} Connected</span>
            <span className={`cam-badge recording ${recordingCameras.length > 0 ? 'active' : ''}`}>
              {recordingCameras.length} Recording
            </span>
          </div>
        </div>
      </div>

      {/* Shoot Context */}
      {activeShoot && (
        <div className="card shoot-context-bar">
          <div className="shoot-badges">
            <span className="shoot-name-badge">{activeShoot.name}</span>
            <span className="shoot-take-badge">
              {isRecording
                ? `Recording Take ${currentTakeNumber}`
                : currentTakeNumber > 0
                  ? `Next: Take ${nextTakeNumber}`
                  : `Ready for Take 1`
              }
            </span>
          </div>
          <div className="shoot-bar-actions">
            <button className="btn btn-secondary btn-sm" onClick={handleEndShoot} disabled={isRecording}>
              End Shoot
            </button>
          </div>
        </div>
      )}

      {/* Shoot Management (collapsed) */}
      {!activeShoot && (
        <div className="card shoot-panel-compact">
          <div className="create-shoot-row">
            <input
              type="text"
              placeholder="Shoot name (e.g., Beach Scene)"
              value={newShootName}
              onChange={(e) => setNewShootName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreateShoot()}
              className="shoot-name-input"
            />
            <button
              className="btn btn-primary btn-sm"
              onClick={handleCreateShoot}
              disabled={!newShootName.trim() || creatingShoot}
            >
              {creatingShoot ? 'Creating...' : 'New Shoot'}
            </button>
            {shoots.length > 0 && (
              <select
                className="shoot-select"
                onChange={(e) => e.target.value && handleSelectShoot(e.target.value)}
                defaultValue=""
              >
                <option value="" disabled>Activate existing shoot...</option>
                {shoots.map(s => (
                  <option key={s.id} value={s.id}>{s.name} ({s.takes?.length || 0} takes)</option>
                ))}
              </select>
            )}
          </div>
        </div>
      )}

      {/* Section B: Camera Health Grid */}
      <div className="health-grid">
        {cameras.map((camera) => {
          const h = healthData[camera.serial] || {};
          const batteryPct = h.battery_percent ?? camera.battery_level;
          const batteryColor = batteryPct == null ? '#555' : batteryPct > 50 ? '#10b981' : batteryPct > 20 ? '#f59e0b' : '#ef4444';
          const storageKB = h.storage_remaining_kb;
          const recDuration = h.recording_duration_sec;
          const thermalState = h.system_hot ? 'hot' : h.thermal_mitigation ? 'warm' : 'normal';
          const gpsLock = h.gps_lock;
          const sdStatus = h.sd_status;
          const isEncoding = h.is_encoding || camera.recording;

          return (
            <div key={camera.serial} className={`health-card ${isEncoding ? 'encoding' : ''} ${!camera.connected ? 'disconnected' : ''}`}>
              <div className="health-card-header">
                <div className="health-cam-name">
                  {camera.name || `GoPro ${camera.serial}`}
                  <span className="health-cam-serial">{camera.serial}</span>
                </div>
                <div className="health-status-dots">
                  {isEncoding && <span className="status-dot recording-dot-pulse" title="Recording"></span>}
                  <span className={`status-dot thermal-${thermalState}`} title={`Thermal: ${thermalState}`}></span>
                  <span className={`status-dot gps-${gpsLock ? 'locked' : 'none'}`} title={gpsLock ? 'GPS Locked' : 'No GPS'}></span>
                </div>
              </div>

              {!camera.connected ? (
                <div className="health-disconnected">Disconnected</div>
              ) : (
                <div className="health-card-body">
                  {/* Battery Gauge */}
                  <div className="health-row">
                    <span className="health-label">Battery</span>
                    <div className="health-bar-container">
                      <div className="health-bar" style={{ width: `${batteryPct || 0}%`, background: batteryColor }}></div>
                    </div>
                    <span className="health-value" style={{ color: batteryColor }}>
                      {batteryPct != null ? `${batteryPct}%` : '--'}
                    </span>
                    {h.battery_drain_rate != null && (
                      <span className="health-drain">{h.battery_drain_rate}%/hr</span>
                    )}
                  </div>

                  {/* Storage Bar */}
                  <div className="health-row">
                    <span className="health-label">Storage</span>
                    <div className="health-bar-container">
                      <div className="health-bar storage-bar" style={{ width: storageKB != null ? '100%' : '0%' }}></div>
                    </div>
                    <span className="health-value">{formatStorage(storageKB)}</span>
                    {h.video_remaining_min != null && (
                      <span className="health-drain">{h.video_remaining_min} min</span>
                    )}
                  </div>

                  {/* Recording Timer */}
                  {isEncoding && (
                    <div className="health-row recording-row">
                      <span className="health-label">REC</span>
                      <span className="health-rec-timer">
                        {recDuration != null ? formatTime(recDuration) : formatTime(recordingTime)}
                      </span>
                    </div>
                  )}

                  {/* Status Badges */}
                  <div className="health-badges">
                    {sdStatus && sdStatus !== 'None' && (
                      <span className={`health-badge ${sdStatus.includes('OK') || sdStatus.includes('ok') ? 'ok' : 'error'}`}>
                        SD: {sdStatus.replace('SdStatus.', '').replace('SD_STATUS_', '')}
                      </span>
                    )}
                    {h.num_videos != null && (
                      <span className="health-badge info">{h.num_videos} videos</span>
                    )}
                    {h.num_photos != null && (
                      <span className="health-badge info">{h.num_photos} photos</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Section C: Quick Stats + Alerts */}
      <div className="card quick-stats-bar">
        <div className="quick-stats">
          <div className="quick-stat">
            <span className="quick-stat-label">Total Recording</span>
            <span className="quick-stat-value">{formatTime(totalRecordingSec || recordingTime * recordingCameras.length)}</span>
          </div>
          <div className="quick-stat">
            <span className="quick-stat-label">Total Storage Free</span>
            <span className="quick-stat-value">{formatStorage(totalStorageKB || null)}</span>
          </div>
        </div>
        {alerts.length > 0 && (
          <div className="alerts-section">
            {alerts.map((alert, i) => (
              <div key={i} className={`alert-badge ${alert.type}`}>{alert.text}</div>
            ))}
          </div>
        )}
      </div>

      {/* Take History (when shoot active) */}
      {activeShoot && activeShoot.takes && activeShoot.takes.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: '0.75rem', color: '#888', fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '1px' }}>Take History</h3>
          <div className="take-list">
            {[...activeShoot.takes].reverse().map((take) => (
              <div key={take.take_number} className={`take-item ${!take.stopped_at ? 'take-active' : ''}`}>
                <div className="take-item-header">
                  <span className="take-number">Take {take.take_number}</span>
                  <span className="take-cameras">{take.cameras?.length || 0} cam{(take.cameras?.length || 0) !== 1 ? 's' : ''}</span>
                </div>
                <div className="take-item-status">
                  {!take.stopped_at ? 'Recording...' : 'Completed'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default RecordingDashboard;
