import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import './PresetManager.css';

const RESOLUTION_OPTIONS = [
  { value: 'RES_5_3_K', label: '5.3K' },
  { value: 'RES_4K', label: '4K' },
  { value: 'RES_4K_4_3', label: '4K 4:3' },
  { value: 'RES_2_7K', label: '2.7K' },
  { value: 'RES_1440', label: '1440' },
  { value: 'RES_1080', label: '1080' },
];

const FPS_OPTIONS = [
  { value: 'FPS_240', label: '240' },
  { value: 'FPS_120', label: '120' },
  { value: 'FPS_60', label: '60' },
  { value: 'FPS_30', label: '30' },
  { value: 'FPS_24', label: '24' },
];

const FOV_OPTIONS = [
  { value: 'WIDE', label: 'Wide' },
  { value: 'LINEAR', label: 'Linear' },
  { value: 'NARROW', label: 'Narrow' },
  { value: 'SUPERVIEW', label: 'SuperView' },
];

const HYPERSMOOTH_OPTIONS = [
  { value: 'OFF', label: 'Off' },
  { value: 'STANDARD', label: 'Standard' },
  { value: 'HIGH', label: 'High' },
  { value: 'BOOST', label: 'Boost' },
];

const ANTI_FLICKER_OPTIONS = [
  { value: 'HZ_60', label: '60Hz' },
  { value: 'HZ_50', label: '50Hz' },
];

function PresetManager({ cameras, apiUrl, cohnStatus }) {
  const [presets, setPresets] = useState({});
  const [selectedPreset, setSelectedPreset] = useState('');
  const [message, setMessage] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [applying, setApplying] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [captureCamera, setCaptureCamera] = useState('');
  const [captureName, setCaptureName] = useState('');
  const [gpsEnabled, setGpsEnabled] = useState(true);
  const [applyTarget, setApplyTarget] = useState('all');

  // Form state for creating new preset
  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState('');
  const [formSettings, setFormSettings] = useState({
    resolution: 'RES_4K',
    fps: 'FPS_30',
    video_fov: 'LINEAR',
    hypersmooth: 'HIGH',
    anti_flicker: 'HZ_60',
  });

  const connectedCameras = cameras.filter(cam => cam.connected);
  const cohnCameras = Object.entries(cohnStatus || {}).filter(([_, c]) => c.provisioned && c.online);
  const useCohn = cohnCameras.length > 0;

  const fetchPresets = useCallback(async () => {
    try {
      const response = await axios.get(`${apiUrl}/api/presets`);
      const loaded = response.data.presets || {};
      setPresets(loaded);
      // Auto-select "Preset 1" if it exists and nothing selected yet
      if (!selectedPreset && loaded['Preset 1']) {
        setSelectedPreset('Preset 1');
      }
    } catch (error) {
      console.error('Failed to fetch presets:', error);
    }
  }, [apiUrl, selectedPreset]);

  useEffect(() => {
    fetchPresets();
  }, [fetchPresets]);

  useEffect(() => {
    if (connectedCameras.length > 0 && !captureCamera) {
      setCaptureCamera(connectedCameras[0].serial);
    }
  }, [connectedCameras, captureCamera]);

  const handleCreatePreset = async () => {
    if (!formName.trim()) return;
    try {
      await axios.post(`${apiUrl}/api/presets`, {
        name: formName.trim(),
        settings: formSettings,
      });
      setMessage({ type: 'success', text: `Preset "${formName}" saved!` });
      setFormName('');
      setShowForm(false);
      fetchPresets();
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: `Failed to save preset: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleCaptureFromCamera = async () => {
    if (!captureCamera || !captureName.trim()) return;
    setCapturing(true);
    try {
      const response = await axios.post(`${apiUrl}/api/presets/capture/${captureCamera}`, {
        name: captureName.trim(),
      });
      if (response.data.success) {
        setMessage({ type: 'success', text: `Captured settings as "${captureName}"!` });
        setCaptureName('');
        fetchPresets();
      }
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: `Capture failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    } finally {
      setCapturing(false);
    }
  };

  const handleApplyToAll = async () => {
    if (!selectedPreset) return;
    setApplying(true);
    const targetLabel = applyTarget === 'all' ? 'all cameras' : applyTarget;
    try {
      let response;
      if (useCohn) {
        const payload = { settings: presets[selectedPreset] };
        if (applyTarget !== 'all') {
          payload.serials = [applyTarget];
        }
        response = await axios.post(`${apiUrl}/api/cohn/settings/apply`, payload);
      } else {
        if (applyTarget !== 'all') {
          response = await axios.post(`${apiUrl}/api/presets/${encodeURIComponent(selectedPreset)}/apply`, {
            serials: [applyTarget],
          });
        } else {
          response = await axios.post(`${apiUrl}/api/presets/${encodeURIComponent(selectedPreset)}/apply`, {});
        }
      }
      if (response.data.success) {
        setMessage({ type: 'success', text: `Preset "${selectedPreset}" applied to ${targetLabel}!` });
      }
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({ type: 'error', text: `Apply failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    } finally {
      setApplying(false);
    }
  };

  const handleGpsToggle = async () => {
    try {
      await axios.post(`${apiUrl}/api/cohn/settings/apply`, {
        settings: { gps: gpsEnabled ? 'ON' : 'OFF' },
      });
      setMessage({ type: 'success', text: `GPS ${gpsEnabled ? 'enabled' : 'disabled'} on cameras` });
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: `GPS toggle failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleDeletePreset = async (name) => {
    if (!window.confirm(`Delete preset "${name}"?`)) return;
    try {
      await axios.delete(`${apiUrl}/api/presets/${encodeURIComponent(name)}`);
      setMessage({ type: 'success', text: `Preset "${name}" deleted` });
      if (selectedPreset === name) setSelectedPreset('');
      fetchPresets();
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: `Delete failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleTogglePin = async (name) => {
    try {
      const response = await axios.patch(`${apiUrl}/api/presets/${encodeURIComponent(name)}`);
      fetchPresets();
      setMessage({ type: 'success', text: `Preset "${name}" ${response.data.pinned ? 'pinned' : 'unpinned'}` });
      setTimeout(() => setMessage(null), 3000);
    } catch (error) {
      setMessage({ type: 'error', text: `Pin toggle failed: ${error.response?.data?.detail || error.message}` });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const presetNames = Object.keys(presets);
  const currentPresetData = selectedPreset ? presets[selectedPreset] : null;

  const formatSettingValue = (key, value) => {
    if (!value) return '--';
    // Clean up enum-style names for display
    return value
      .replace('RES_', '')
      .replace('FPS_', '')
      .replace('HZ_', '')
      .replace(/_/g, ' ');
  };

  return (
    <div className="preset-manager">
      <div className="preset-header" onClick={() => setExpanded(!expanded)}>
        <h3>Preset Management</h3>
        <span className="preset-toggle">{expanded ? '▼' : '▶'}</span>
      </div>

      {expanded && (
        <div className="preset-content">
          {message && (
            <div className={`alert alert-${message.type}`} style={{ marginBottom: '1rem' }}>
              {message.text}
            </div>
          )}

          {/* Preset List */}
          {presetNames.length > 0 ? (
            <div className="preset-list">
              {presetNames.map(name => {
                const isPinned = presets[name]?.pinned;
                const isSelected = selectedPreset === name;
                return (
                  <div
                    key={name}
                    className={`preset-list-item ${isSelected ? 'preset-list-item--selected' : ''}`}
                    onClick={() => setSelectedPreset(isSelected ? '' : name)}
                  >
                    <button
                      className={`btn-pin ${isPinned ? 'btn-pin--active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); handleTogglePin(name); }}
                      title={isPinned ? 'Unpin' : 'Pin to top'}
                    >
                      {isPinned ? '\u2605' : '\u2606'}
                    </button>
                    <span className="preset-list-name">{name}</span>
                    <button
                      className="btn-delete-preset"
                      onClick={(e) => { e.stopPropagation(); handleDeletePreset(name); }}
                      title="Delete preset"
                    >
                      &times;
                    </button>
                  </div>
                );
              })}
            </div>
          ) : (
            <p style={{ color: 'var(--text-dimmed)', fontStyle: 'italic', fontSize: '0.85rem' }}>
              No presets saved. Capture from a camera or create one manually below.
            </p>
          )}

          {/* Apply Controls */}
          {selectedPreset && (
            <div className="preset-select-row">
              <select
                className="apply-target-select"
                value={applyTarget}
                onChange={(e) => setApplyTarget(e.target.value)}
              >
                <option value="all">All Cameras</option>
                {connectedCameras.map(cam => (
                  <option key={cam.serial} value={cam.serial}>
                    {cam.name || `GoPro ${cam.serial}`}
                  </option>
                ))}
              </select>

              <button
                className="btn btn-primary btn-sm"
                onClick={handleApplyToAll}
                disabled={!selectedPreset || connectedCameras.length === 0 || applying}
              >
                {applying ? 'Applying...' : applyTarget === 'all' ? 'Apply to All' : 'Apply'}
              </button>

              <span
                className="transport-indicator"
                style={{ color: useCohn ? '#2ecc71' : '#999', fontWeight: 'bold', fontSize: '0.85em', whiteSpace: 'nowrap' }}
              >
                {useCohn ? 'via COHN' : 'via BLE'}
              </span>
            </div>
          )}

          {/* Selected Preset Details */}
          {currentPresetData && (
            <div className="preset-details">
              <div className="preset-detail-grid">
                <div className="preset-detail-item">
                  <span className="detail-label">Resolution</span>
                  <span className="detail-value">{formatSettingValue('resolution', currentPresetData.resolution)}</span>
                </div>
                <div className="preset-detail-item">
                  <span className="detail-label">FPS</span>
                  <span className="detail-value">{formatSettingValue('fps', currentPresetData.fps)}</span>
                </div>
                <div className="preset-detail-item">
                  <span className="detail-label">FOV</span>
                  <span className="detail-value">{formatSettingValue('video_fov', currentPresetData.video_fov)}</span>
                </div>
                <div className="preset-detail-item">
                  <span className="detail-label">Hypersmooth</span>
                  <span className="detail-value">{formatSettingValue('hypersmooth', currentPresetData.hypersmooth)}</span>
                </div>
                <div className="preset-detail-item">
                  <span className="detail-label">Anti-Flicker</span>
                  <span className="detail-value">{formatSettingValue('anti_flicker', currentPresetData.anti_flicker)}</span>
                </div>
                {currentPresetData.shutter && (
                  <div className="preset-detail-item">
                    <span className="detail-label">Shutter</span>
                    <span className="detail-value">{formatSettingValue('shutter', currentPresetData.shutter)}</span>
                  </div>
                )}
              </div>

              <div className="gps-toggle-row" style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <label style={{ fontWeight: 'bold', fontSize: '0.85em' }}>GPS</label>
                <button
                  className={`btn btn-sm ${gpsEnabled ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setGpsEnabled(prev => !prev)}
                  style={{ minWidth: '50px' }}
                >
                  {gpsEnabled ? 'ON' : 'OFF'}
                </button>
                <button
                  className="btn btn-sm btn-secondary"
                  onClick={handleGpsToggle}
                  disabled={!useCohn}
                  title={!useCohn ? 'Requires COHN connection' : 'Apply GPS setting to cameras'}
                >
                  Apply GPS
                </button>
              </div>
            </div>
          )}

          {/* Capture from Camera */}
          {connectedCameras.length > 0 && (
            <div className="preset-capture-section">
              <h4>Capture from Camera</h4>
              <div className="capture-row">
                <select
                  className="capture-camera-select"
                  value={captureCamera}
                  onChange={(e) => setCaptureCamera(e.target.value)}
                >
                  {connectedCameras.map(cam => (
                    <option key={cam.serial} value={cam.serial}>
                      {cam.name || `GoPro ${cam.serial}`}
                    </option>
                  ))}
                </select>
                <input
                  type="text"
                  className="capture-name-input"
                  placeholder="Preset name"
                  value={captureName}
                  onChange={(e) => setCaptureName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCaptureFromCamera()}
                />
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={handleCaptureFromCamera}
                  disabled={!captureCamera || !captureName.trim() || capturing}
                >
                  {capturing ? 'Capturing...' : 'Capture'}
                </button>
              </div>
            </div>
          )}

          {/* Create Preset Form */}
          <div className="preset-create-section">
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setShowForm(!showForm)}
            >
              {showForm ? 'Cancel' : '+ Create Preset Manually'}
            </button>

            {showForm && (
              <div className="preset-form">
                <input
                  type="text"
                  className="preset-form-name"
                  placeholder="Preset name"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                />

                <div className="preset-form-grid">
                  <div className="form-field">
                    <label>Resolution</label>
                    <select value={formSettings.resolution} onChange={(e) => setFormSettings(s => ({ ...s, resolution: e.target.value }))}>
                      {RESOLUTION_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="form-field">
                    <label>FPS</label>
                    <select value={formSettings.fps} onChange={(e) => setFormSettings(s => ({ ...s, fps: e.target.value }))}>
                      {FPS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="form-field">
                    <label>Video FOV</label>
                    <select value={formSettings.video_fov} onChange={(e) => setFormSettings(s => ({ ...s, video_fov: e.target.value }))}>
                      {FOV_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="form-field">
                    <label>Hypersmooth</label>
                    <select value={formSettings.hypersmooth} onChange={(e) => setFormSettings(s => ({ ...s, hypersmooth: e.target.value }))}>
                      {HYPERSMOOTH_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="form-field">
                    <label>Anti-Flicker</label>
                    <select value={formSettings.anti_flicker} onChange={(e) => setFormSettings(s => ({ ...s, anti_flicker: e.target.value }))}>
                      {ANTI_FLICKER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </div>

                <button
                  className="btn btn-primary btn-sm"
                  onClick={handleCreatePreset}
                  disabled={!formName.trim()}
                >
                  Save Preset
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default PresetManager;
