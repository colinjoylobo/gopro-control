import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './RecordingControl.css';

function RecordingControl({ cameras, onCamerasUpdate, apiUrl, activeShoot, onShootUpdate }) {
  const [recordingTime, setRecordingTime] = useState(0);
  const [message, setMessage] = useState(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [recordingStartedAt, setRecordingStartedAt] = useState(null);

  // Shoot management state
  const [shoots, setShoots] = useState([]);
  const [newShootName, setNewShootName] = useState('');
  const [creatingShoot, setCreatingShoot] = useState(false);

  const connectedCameras = cameras.filter(cam => cam.connected);
  const recordingCameras = cameras.filter(cam => cam.recording);

  // Derive isRecording from actual camera data — survives tab switches
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
      console.error('Failed to create shoot:', error);
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
      console.error('Failed to activate shoot:', error);
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
      console.error('Failed to end shoot:', error);
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
      console.error('Failed to delete shoot:', error);
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

  const handleStartRecording = async () => {
    if (connectedCameras.length === 0) {
      setMessage({ type: 'error', text: 'No cameras connected! Go to Camera Management to connect cameras.' });
      setTimeout(() => setMessage(null), 5000);
      return;
    }

    setStarting(true);
    setMessage({ type: 'info', text: `Starting recording on ${connectedCameras.length} cameras...` });

    try {
      console.log('Starting recording...', connectedCameras.map(c => c.serial));
      const response = await axios.post(`${apiUrl}/api/recording/start`, null, {
        timeout: 30000 // 30 second timeout
      });

      const results = response.data.results;
      console.log('Start recording results:', results);

      const successCount = Object.values(results).filter(r => r).length;
      const totalCount = Object.keys(results).length;

      if (successCount > 0) {
        const takeInfo = response.data.take ? ` (Take ${response.data.take.take_number})` : '';
        setMessage({
          type: 'success',
          text: `Recording started on ${successCount}/${totalCount} cameras!${takeInfo}`
        });
      } else {
        setMessage({ type: 'error', text: 'Failed to start recording on any camera. Check terminal logs.' });
      }

      // Force UI update
      await onCamerasUpdate();
      onShootUpdate();
      setTimeout(() => onCamerasUpdate(), 1000);

      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      console.error('Start recording error:', error);
      setMessage({
        type: 'error',
        text: `Failed to start recording: ${error.response?.data?.detail || error.message}. Check terminal logs.`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setStarting(false);
    }
  };

  const handleStopRecording = async () => {
    setStopping(true);
    setMessage({ type: 'info', text: 'Stopping recording on all cameras...' });

    try {
      console.log('Stopping recording...', recordingCameras.map(c => c.serial));
      const response = await axios.post(`${apiUrl}/api/recording/stop`, null, {
        timeout: 30000 // 30 second timeout
      });

      const results = response.data.results;
      console.log('Stop recording results:', results);

      const successCount = Object.values(results).filter(r => r).length;
      const totalCount = Object.keys(results).length;

      setMessage({
        type: 'success',
        text: `Recording stopped on ${successCount}/${totalCount} cameras! Waiting 5 seconds for files to save...`
      });

      // Force UI update
      await onCamerasUpdate();
      onShootUpdate();

      // Update again after file save delay
      setTimeout(() => {
        onCamerasUpdate();
        setMessage({ type: 'success', text: 'Files saved! You can now download them.' });
        setTimeout(() => setMessage(null), 5000);
      }, 5000);

    } catch (error) {
      console.error('Stop recording error:', error);
      setMessage({
        type: 'error',
        text: `Failed to stop recording: ${error.response?.data?.detail || error.message}. Check terminal logs.`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setStopping(false);
    }
  };

  // Compute current/next take number
  const currentTakeNumber = activeShoot ? activeShoot.current_take_number : 0;
  const nextTakeNumber = currentTakeNumber + 1;

  return (
    <div className="recording-control">
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      {/* Shoot Management Panel */}
      <div className="card shoot-panel">
        <h2>Shoot Management</h2>

        {activeShoot ? (
          <div className="shoot-active-section">
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

            {activeShoot.takes && activeShoot.takes.length > 0 && (
              <div className="take-history">
                <h3>Take History</h3>
                <div className="take-list">
                  {[...activeShoot.takes].reverse().map((take) => (
                    <div
                      key={take.take_number}
                      className={`take-item ${!take.stopped_at ? 'take-active' : ''}`}
                    >
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

            <div className="shoot-actions">
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  // Show shoot list to switch
                  fetchShoots();
                }}
              >
                Switch Shoot
              </button>
              <button
                className="btn btn-secondary btn-sm"
                onClick={handleEndShoot}
                disabled={isRecording}
                title={isRecording ? 'Stop recording before ending shoot' : 'End this shoot'}
              >
                End Shoot
              </button>
            </div>

            {/* Existing shoots list for switching */}
            {shoots.length > 1 && (
              <div className="shoots-list" style={{ marginTop: '1rem' }}>
                <h4 style={{ color: '#888', marginBottom: '0.5rem', fontSize: '0.85rem' }}>Switch to:</h4>
                {shoots.filter(s => s.id !== activeShoot.id).map(shoot => (
                  <div key={shoot.id} className="shoot-list-item">
                    <span className="shoot-list-name">{shoot.name}</span>
                    <span className="shoot-list-takes">{shoot.takes?.length || 0} takes</span>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => handleSelectShoot(shoot.id)}
                      disabled={isRecording}
                    >
                      Activate
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="shoot-inactive-section">
            <p className="shoot-hint">
              Create or select a shoot to organize recordings into takes. Recording without a shoot works the same as before.
            </p>

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
            </div>

            {shoots.length > 0 && (
              <div className="shoots-list">
                <h4 style={{ color: '#888', marginBottom: '0.5rem', fontSize: '0.85rem' }}>Existing Shoots</h4>
                {shoots.map(shoot => (
                  <div key={shoot.id} className="shoot-list-item">
                    <span className="shoot-list-name">{shoot.name}</span>
                    <span className="shoot-list-takes">{shoot.takes?.length || 0} takes</span>
                    <div className="shoot-list-actions">
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => handleSelectShoot(shoot.id)}
                      >
                        Activate
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => handleDeleteShoot(shoot.id, shoot.name)}
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <h2>Recording Status</h2>

        {/* Shoot context in recording status */}
        {activeShoot && (
          <div className="recording-shoot-context">
            {isRecording
              ? `Recording Take ${currentTakeNumber} of "${activeShoot.name}"`
              : currentTakeNumber > 0
                ? `Next: Take ${nextTakeNumber} of "${activeShoot.name}"`
                : `Ready for Take 1 of "${activeShoot.name}"`
            }
          </div>
        )}

        <div className="recording-status">
          <div className="status-display">
            <div className={`recording-indicator ${isRecording ? 'active' : ''}`}>
              {isRecording && <span className="pulse-dot"></span>}
              <span className="status-text">
                {isRecording ? 'RECORDING' : 'READY'}
              </span>
            </div>

            {isRecording && (
              <div className="recording-timer">
                {formatTime(recordingTime)}
              </div>
            )}
          </div>

          <div className="camera-status-grid">
            <div className="stat-card">
              <div className="stat-value">{cameras.length}</div>
              <div className="stat-label">Total Cameras</div>
            </div>
            <div className="stat-card">
              <div className="stat-value">{connectedCameras.length}</div>
              <div className="stat-label">Connected</div>
            </div>
            <div className="stat-card">
              <div className="stat-value recording">{recordingCameras.length}</div>
              <div className="stat-label">Recording</div>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Recording Controls</h2>

        {connectedCameras.length === 0 ? (
          <div className="alert alert-info">
            No cameras connected. Please go to "Camera Management" tab and connect your cameras first.
          </div>
        ) : (
          <div className="recording-controls">
            <button
              className={`btn btn-record ${isRecording ? 'recording' : ''}`}
              onClick={handleStartRecording}
              disabled={isRecording || starting}
            >
              {starting ? (
                <>
                  <span className="spinner"></span>
                  Starting...
                </>
              ) : (
                <>
                  <span className="record-icon">⏺</span>
                  {activeShoot ? `Start Take ${nextTakeNumber}` : 'Start Recording'}
                </>
              )}
            </button>

            <button
              className="btn btn-stop"
              onClick={handleStopRecording}
              disabled={!isRecording || stopping}
            >
              {stopping ? (
                <>
                  <span className="spinner"></span>
                  Stopping...
                </>
              ) : (
                <>
                  <span className="stop-icon">⏹</span>
                  Stop Recording
                </>
              )}
            </button>
          </div>
        )}
      </div>

      {connectedCameras.length > 0 && (
        <div className="card">
          <h2>Connected Cameras ({connectedCameras.length})</h2>

          <div className="cameras-list">
            {connectedCameras.map((camera) => (
              <div key={camera.serial} className="camera-item">
                <div className="camera-item-info">
                  <div className="camera-name">
                    {camera.name || `GoPro ${camera.serial}`}
                  </div>
                  <div className="camera-serial">Serial: {camera.serial}</div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  {camera.battery_level !== null && camera.battery_level !== undefined && (
                    <span style={{
                      fontSize: '0.85rem',
                      color: camera.battery_level > 50 ? '#28a745' : camera.battery_level > 20 ? '#ffc107' : '#dc3545',
                      fontWeight: 'bold'
                    }}>
                      {camera.battery_level > 20 ? '🔋' : '🪫'} {camera.battery_level}%
                    </span>
                  )}
                  <div className={`camera-item-status ${camera.recording ? 'recording' : ''}`}>
                    {camera.recording ? (
                      <>
                        <span className="recording-dot"></span>
                        Recording
                      </>
                    ) : (
                      'Ready'
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h2>Instructions</h2>
        <ol className="instructions-list">
          <li>Ensure all cameras are connected via BLE (check Camera Management tab)</li>
          <li>(Optional) Create a shoot to organize recordings into named takes</li>
          <li>Click "Start Recording" to begin recording on all connected cameras</li>
          <li>Recording will start simultaneously on all cameras</li>
          <li>Click "Stop Recording" when finished</li>
          <li>Wait for files to save (takes a few seconds after stopping)</li>
          <li>Go to "Download & Upload" tab to retrieve your videos</li>
        </ol>
      </div>
    </div>
  );
}

export default RecordingControl;
