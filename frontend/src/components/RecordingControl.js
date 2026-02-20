import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './RecordingControl.css';

function RecordingControl({ cameras, onCamerasUpdate, apiUrl }) {
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [message, setMessage] = useState(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);

  const connectedCameras = cameras.filter(cam => cam.connected);
  const recordingCameras = cameras.filter(cam => cam.recording);

  // Timer for recording duration
  useEffect(() => {
    let interval;
    if (isRecording) {
      interval = setInterval(() => {
        setRecordingTime(prev => prev + 1);
      }, 1000);
    } else {
      setRecordingTime(0);
    }
    return () => clearInterval(interval);
  }, [isRecording]);

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
        setIsRecording(true);
        setMessage({
          type: 'success',
          text: `🔴 Recording started on ${successCount}/${totalCount} cameras!`
        });
      } else {
        setMessage({ type: 'error', text: 'Failed to start recording on any camera. Check terminal logs.' });
      }

      // Force UI update
      await onCamerasUpdate();
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

      setIsRecording(false);
      setMessage({
        type: 'success',
        text: `⏹️ Recording stopped on ${successCount}/${totalCount} cameras! Waiting 5 seconds for files to save...`
      });

      // Force UI update
      await onCamerasUpdate();

      // Update again after file save delay
      setTimeout(() => {
        onCamerasUpdate();
        setMessage({ type: 'success', text: 'Files saved! You can now download them.' });
        setTimeout(() => setMessage(null), 5000);
      }, 5000);

    } catch (error) {
      console.error('Stop recording error:', error);
      setIsRecording(false);
      setMessage({
        type: 'error',
        text: `Failed to stop recording: ${error.response?.data?.detail || error.message}. Check terminal logs.`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setStopping(false);
    }
  };

  return (
    <div className="recording-control">
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      <div className="card">
        <h2>Recording Status</h2>

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
                  Start Recording
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
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h2>Instructions</h2>
        <ol className="instructions-list">
          <li>Ensure all cameras are connected via BLE (check Camera Management tab)</li>
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
