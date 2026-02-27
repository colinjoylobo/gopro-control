import React, { useState } from 'react';
import axios from 'axios';
import PresetManager from './PresetManager';
import './CameraManagement.css';

function CameraManagement({ cameras, onCamerasUpdate, apiUrl, cohnStatus }) {
  const [showAddForm, setShowAddForm] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectingSerial, setConnectingSerial] = useState(null);
  const [message, setMessage] = useState(null);
  const [discoveredCameras, setDiscoveredCameras] = useState([]);
  const [editingName, setEditingName] = useState(null);
  const [editNameValue, setEditNameValue] = useState('');

  // Grid layout: 'auto' = smart (2 cols even, 3 cols odd), '2col', '3col'
  const [gridLayout, setGridLayout] = useState(() => localStorage.getItem('cameras_grid_layout') || 'auto');

  const handleGridLayout = (layout) => {
    setGridLayout(layout);
    localStorage.setItem('cameras_grid_layout', layout);
  };

  const getGridClass = () => {
    if (gridLayout === '2col') return 'grid-2col';
    if (gridLayout === '3col') return 'grid-3col';
    return cameras.length % 2 === 0 ? 'grid-2col' : 'grid-3col';
  };

  // Battery comes from camera props (updated via App.js WebSocket + polling)
  const getBatteryColor = (level) => {
    if (level === null || level === undefined) return '#999';
    if (level > 50) return '#28a745';
    if (level > 20) return '#ffc107';
    return '#dc3545';
  };

  const [newCamera, setNewCamera] = useState({
    serial: '',
    name: '',
    wifi_ssid: '',
    wifi_password: ''
  });

  const handleAddCamera = async (e) => {
    e.preventDefault();

    try {
      await axios.post(`${apiUrl}/api/cameras`, newCamera);
      setMessage({ type: 'success', text: 'Camera added successfully!' });
      setNewCamera({ serial: '', name: '', wifi_ssid: '', wifi_password: '' });
      setShowAddForm(false);
      onCamerasUpdate();

      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: error.response?.data?.detail || 'Failed to add camera' });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleRemoveCamera = async (serial) => {
    if (!window.confirm('Are you sure you want to remove this camera?')) {
      return;
    }

    try {
      await axios.delete(`${apiUrl}/api/cameras/${serial}`);
      setMessage({ type: 'success', text: 'Camera removed successfully!' });
      onCamerasUpdate();

      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: 'Failed to remove camera' });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleDiscoverCameras = async () => {
    setDiscovering(true);
    setDiscoveredCameras([]);
    setMessage({ type: 'info', text: 'Scanning for GoPro cameras via Bluetooth... This may take 30 seconds. Keep cameras powered on!' });

    try {
      const response = await axios.post(`${apiUrl}/api/cameras/discover`, null, {
        params: { timeout: 30 }
      });

      const discovered = response.data.cameras;

      if (discovered.length === 0) {
        setMessage({ type: 'info', text: 'No GoPro cameras found. Ensure cameras are on, Bluetooth is enabled, and permissions are granted.' });
      } else {
        setDiscoveredCameras(discovered);
        setMessage({
          type: 'success',
          text: `Found ${discovered.length} GoPro camera(s)! Click on a camera below to add it.`
        });
        console.log('Discovered cameras:', discovered);
      }

      setTimeout(() => setMessage(null), 8000);
    } catch (error) {
      console.error('Discovery error:', error);
      setMessage({
        type: 'error',
        text: `Discovery failed: ${error.response?.data?.detail || 'Check Bluetooth permissions in System Settings'}`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setDiscovering(false);
    }
  };

  const handleUseDiscoveredCamera = (discovered) => {
    setNewCamera({
      serial: discovered.serial,
      name: discovered.name || `GoPro ${discovered.serial}`,
      wifi_ssid: `GP${discovered.serial}`,  // Common GoPro WiFi pattern
      wifi_password: ''
    });
    setShowAddForm(true);
    setDiscoveredCameras([]);
  };

  const handleConnectSingle = async (serial) => {
    setConnectingSerial(serial);
    setMessage({ type: 'info', text: `Connecting to ${serial} via BLE...` });

    try {
      const response = await axios.post(`${apiUrl}/api/cameras/connect/${serial}`, null, {
        timeout: 90000
      });

      if (response.data.success) {
        setMessage({ type: 'success', text: `Camera ${serial} connected!` });
      } else {
        setMessage({ type: 'error', text: `Camera ${serial} failed to connect. Check if it's powered on.` });
      }

      await onCamerasUpdate();
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({
        type: 'error',
        text: `Connect ${serial} failed: ${error.response?.data?.detail || error.message}`
      });
      setTimeout(() => setMessage(null), 5000);
    } finally {
      setConnectingSerial(null);
    }
  };

  const handleConnectAll = async () => {
    if (cameras.length === 0) {
      setMessage({ type: 'error', text: 'No cameras to connect! Add cameras first.' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    // Check how many cameras are already connected
    const alreadyConnected = cameras.filter(c => c.connected);
    const needConnection = cameras.filter(c => !c.connected);

    if (needConnection.length === 0) {
      setMessage({ type: 'success', text: `All ${cameras.length} camera(s) are already connected!` });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    setConnecting(true);
    if (alreadyConnected.length > 0) {
      setMessage({
        type: 'info',
        text: `${alreadyConnected.length} already connected. Connecting to ${needConnection.length} camera(s)... Make sure cameras are powered ON!`
      });
    } else {
      setMessage({ type: 'info', text: `Connecting to ${cameras.length} camera(s) via BLE... Make sure cameras are powered ON!` });
    }

    try {
      console.log('Attempting to connect to cameras:', cameras.map(c => c.serial));

      const response = await axios.post(`${apiUrl}/api/cameras/connect-all`, null, {
        timeout: 300000 // 5 minute timeout
      });

      const results = response.data.results;
      console.log('Connection results:', results);

      const successCount = Object.values(results).filter(r => r).length;
      const totalCount = Object.keys(results).length;

      if (successCount === 0) {
        setMessage({
          type: 'error',
          text: '❌ Connection timeout! On each camera: Swipe down → Connections → Reset Connections. Then try again. See PAIRING_GUIDE.md for help.'
        });
      } else if (successCount === totalCount) {
        setMessage({ type: 'success', text: `All ${successCount} cameras connected!` });
      } else {
        const failed = Object.entries(results).filter(([_, success]) => !success).map(([serial, _]) => serial);
        setMessage({
          type: 'info',
          text: `Connected ${successCount}/${totalCount} cameras. Failed: ${failed.join(', ')}. Check terminal logs for details.`
        });
      }

      // Force immediate update of camera list
      console.log('Connection complete, forcing camera list refresh...');
      await onCamerasUpdate();

      // Wait a bit for state to settle, then refresh again
      setTimeout(async () => {
        console.log('Secondary refresh after connection...');
        await onCamerasUpdate();
      }, 1000);

      setTimeout(() => setMessage(null), 8000);
    } catch (error) {
      console.error('Connection error:', error);
      setMessage({
        type: 'error',
        text: `Connection failed: ${error.response?.data?.detail || error.message}. Check terminal logs.`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setConnecting(false);
    }
  };

  const handleSaveName = async (serial) => {
    try {
      await axios.patch(`${apiUrl}/api/cameras/${serial}`, { name: editNameValue });
      setEditingName(null);
      setEditNameValue('');
      onCamerasUpdate();
    } catch (error) {
      setMessage({ type: 'error', text: error.response?.data?.detail || 'Failed to update camera name' });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleDisconnectSingle = async (serial) => {
    try {
      await axios.post(`${apiUrl}/api/cameras/disconnect/${serial}`);
      setMessage({ type: 'success', text: `Camera ${serial} disconnected.` });
      onCamerasUpdate();
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: error.response?.data?.detail || `Failed to disconnect camera ${serial}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleDisconnectAll = async () => {
    try {
      await axios.post(`${apiUrl}/api/cameras/disconnect-all`);
      setMessage({ type: 'success', text: 'All cameras disconnected!' });
      onCamerasUpdate();
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: 'Disconnect failed' });
      setTimeout(() => setMessage(null), 3000);
    }
  };

  return (
    <div className="camera-management">
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h2>Camera List ({cameras.length})</h2>
          <div className="button-group">
            <button
              className="btn btn-secondary"
              onClick={() => {
                console.log('Manual refresh clicked');
                onCamerasUpdate();
              }}
              title="Refresh camera list"
            >
              🔄 Refresh
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleDiscoverCameras}
              disabled={discovering}
            >
              {discovering ? 'Discovering...' : 'Auto-Discover'}
            </button>
            <button
              className="btn btn-primary"
              onClick={() => setShowAddForm(!showAddForm)}
            >
              {showAddForm ? 'Cancel' : 'Add Camera'}
            </button>
          </div>
        </div>

        {discoveredCameras.length > 0 && (
          <div className="discovered-cameras">
            <h3>Discovered Cameras ({discoveredCameras.length})</h3>
            <p style={{ color: '#888', marginBottom: '1rem' }}>
              Click on a camera to add it to your list (you'll need to provide WiFi password)
            </p>
            <div className="discovered-grid">
              {discoveredCameras.map((camera, idx) => (
                <div
                  key={idx}
                  className="discovered-camera-card"
                  onClick={() => handleUseDiscoveredCamera(camera)}
                >
                  <div className="discovered-icon">📷</div>
                  <div className="discovered-info">
                    <div className="discovered-name">{camera.name}</div>
                    <div className="discovered-serial">Serial: {camera.serial}</div>
                    <div className="discovered-address">{camera.address}</div>
                  </div>
                  <div className="discovered-action">Click to add →</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {showAddForm && (
          <form onSubmit={handleAddCamera} className="add-camera-form">
            <div className="form-row">
              <div className="form-group">
                <label>Camera Serial (Last 4 digits)</label>
                <input
                  type="text"
                  placeholder="e.g., 8881"
                  value={newCamera.serial}
                  onChange={(e) => setNewCamera({ ...newCamera, serial: e.target.value })}
                  required
                />
              </div>

              <div className="form-group">
                <label>Camera Name (Optional)</label>
                <input
                  type="text"
                  placeholder="e.g., Front Camera"
                  value={newCamera.name}
                  onChange={(e) => setNewCamera({ ...newCamera, name: e.target.value })}
                />
              </div>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>WiFi SSID</label>
                <input
                  type="text"
                  placeholder="e.g., GP25468881"
                  value={newCamera.wifi_ssid}
                  onChange={(e) => setNewCamera({ ...newCamera, wifi_ssid: e.target.value })}
                  required
                />
              </div>

              <div className="form-group">
                <label>WiFi Password</label>
                <input
                  type="password"
                  placeholder="WiFi password"
                  value={newCamera.wifi_password}
                  onChange={(e) => setNewCamera({ ...newCamera, wifi_password: e.target.value })}
                  required
                />
              </div>
            </div>

            <button type="submit" className="btn btn-success">
              Add Camera
            </button>
          </form>
        )}

        {cameras.length === 0 ? (
          <div className="empty-state">
            <p>No cameras added yet. Click "Add Camera" or "Auto-Discover" to get started.</p>
          </div>
        ) : (
          <>
          <div className="grid-layout-bar">
            <span className="layout-label">Layout</span>
            <button className={`layout-btn ${gridLayout === 'auto' ? 'active' : ''}`} onClick={() => handleGridLayout('auto')}>Auto</button>
            <button className={`layout-btn ${gridLayout === '2col' ? 'active' : ''}`} onClick={() => handleGridLayout('2col')}>2-Col</button>
            <button className={`layout-btn ${gridLayout === '3col' ? 'active' : ''}`} onClick={() => handleGridLayout('3col')}>3-Col</button>
          </div>
          <div className={`cameras-grid ${getGridClass()}`}>
            {[...cameras].sort((a, b) => {
              if (a.connected && !b.connected) return -1;
              if (!a.connected && b.connected) return 1;
              return 0;
            }).map((camera) => (
              <div key={camera.serial} className="camera-card">
                <div className="camera-header">
                  <div>
                    {editingName === camera.serial ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <input
                          type="text"
                          value={editNameValue}
                          onChange={(e) => setEditNameValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSaveName(camera.serial);
                            if (e.key === 'Escape') { setEditingName(null); setEditNameValue(''); }
                          }}
                          autoFocus
                          style={{ padding: '0.25rem 0.5rem', fontSize: '1rem', borderRadius: '4px', border: '1px solid #555', background: '#2a2a2a', color: 'white' }}
                        />
                        <button className="btn btn-success btn-sm" onClick={() => handleSaveName(camera.serial)}>Save</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => { setEditingName(null); setEditNameValue(''); }}>Cancel</button>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <h3>{camera.name || `GoPro ${camera.serial}`}</h3>
                        <button
                          onClick={() => { setEditingName(camera.serial); setEditNameValue(camera.name || `GoPro ${camera.serial}`); }}
                          style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1rem', padding: '0.1rem 0.3rem' }}
                          title="Edit camera name"
                        >
                          ✏️
                        </button>
                      </div>
                    )}
                    <p className="camera-serial">Serial: {camera.serial}</p>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.25rem' }}>
                    <div className={`connection-badge ${camera.connected ? 'connected' : 'disconnected'}`}>
                      {camera.connected ? 'Connected' : 'Disconnected'}
                    </div>
                    {camera.battery_level !== null && camera.battery_level !== undefined && (
                      <div style={{
                        padding: '0.2rem 0.5rem',
                        borderRadius: '12px',
                        fontSize: '0.8rem',
                        fontWeight: 'bold',
                        background: getBatteryColor(camera.battery_level) + '20',
                        color: getBatteryColor(camera.battery_level),
                        border: `1px solid ${getBatteryColor(camera.battery_level)}40`
                      }}>
                        {camera.battery_level > 20 ? '🔋' : '🪫'} {camera.battery_level}%
                      </div>
                    )}
                  </div>
                </div>

                <div className="camera-info">
                  <div className="info-row">
                    <span className="label">WiFi SSID:</span>
                    <span className="value">{camera.wifi_ssid}</span>
                  </div>
                  <div className="info-row">
                    <span className="label">Status:</span>
                    <span className="value">
                      {camera.recording ? '🔴 Recording' : 'Ready'}
                    </span>
                  </div>
                </div>

                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  {camera.connected ? (
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => handleDisconnectSingle(camera.serial)}
                    >
                      Disconnect
                    </button>
                  ) : (
                    <button
                      className="btn btn-success btn-sm"
                      onClick={() => handleConnectSingle(camera.serial)}
                      disabled={connectingSerial === camera.serial || connecting}
                    >
                      {connectingSerial === camera.serial ? 'Connecting...' : 'Connect'}
                    </button>
                  )}
                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => handleRemoveCamera(camera.serial)}
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
          </>
        )}
      </div>

      {cameras.length > 0 && (
        <div className="card">
          <h2>Connection Control</h2>

          <div className="alert alert-info" style={{ marginBottom: '1rem' }}>
            <strong>Before connecting:</strong>
            <ul style={{ margin: '0.5rem 0', paddingLeft: '1.5rem' }}>
              <li>Make sure each GoPro is <strong>powered ON</strong></li>
              <li>Camera should be on the <strong>home screen</strong> (not in menus)</li>
              <li>If connection times out: On camera, swipe down → Connections → Reset Connections</li>
            </ul>
          </div>

          <div className="button-group">
            <button
              className="btn btn-success"
              onClick={handleConnectAll}
              disabled={connecting}
            >
              {connecting ? 'Connecting...' : 'Connect All Cameras'}
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleDisconnectAll}
            >
              Disconnect All
            </button>
          </div>
        </div>
      )}
      {/* Preset Management */}
      <PresetManager cameras={cameras} apiUrl={apiUrl} cohnStatus={cohnStatus} />

    </div>
  );
}

export default CameraManagement;
