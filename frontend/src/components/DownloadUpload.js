import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './DownloadUpload.css';

function DownloadUpload({ cameras, apiUrl }) {
  const [downloadedFiles, setDownloadedFiles] = useState([]);
  const [downloading, setDownloading] = useState(false);
  const [uploadConfig, setUploadConfig] = useState({
    backend_url: 'https://tinify-backend-dev-868570596092.asia-south1.run.app/api/upload-file',
    api_key: 'juniordevKey@9911'
  });
  const [uploadingFiles, setUploadingFiles] = useState(new Set());
  const [message, setMessage] = useState(null);
  const [downloadProgress, setDownloadProgress] = useState({});
  const [uploadProgress, setUploadProgress] = useState(null); // { current: 1, total: 8, filename: "file.mp4" }
  const [ws, setWs] = useState(null);
  const [currentWiFi, setCurrentWiFi] = useState(null);
  const [uploadedZips, setUploadedZips] = useState([]); // Store all uploaded ZIP URLs

  const connectedCameras = cameras.filter(cam => cam.connected);

  useEffect(() => {
    fetchDownloadedFiles();
    fetchCurrentWiFi();
    connectWebSocket();

    // Refresh WiFi status every 5 seconds
    const wifiInterval = setInterval(fetchCurrentWiFi, 5000);

    return () => {
      if (ws) {
        ws.close();
      }
      clearInterval(wifiInterval);
    };
  }, []);

  const connectWebSocket = () => {
    const websocket = new WebSocket('ws://127.0.0.1:8000/ws');

    websocket.onopen = () => {
      console.log('Download WebSocket connected');
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
      console.log('WebSocket closed');
    };
  };

  const handleWebSocketMessage = (data) => {
    if (data.type === 'download_status') {
      // Show WiFi connection status
      if (data.status === 'connecting_wifi') {
        setMessage({ type: 'info', text: data.message || 'Connecting to camera WiFi...' });
      } else if (data.status === 'wifi_connected') {
        setMessage({ type: 'success', text: data.message || 'WiFi connected! Starting download...' });
      } else if (data.status === 'reconnecting_wifi') {
        setMessage({ type: 'info', text: data.message || 'Reconnecting to internet WiFi...' });
      } else if (data.status === 'wifi_restored') {
        setMessage({ type: 'success', text: data.message || 'WiFi restored! Ready to upload.' });
        fetchCurrentWiFi();  // Refresh WiFi status
      } else if (data.status === 'wifi_manual_needed') {
        setMessage({ type: 'warning', text: data.message || 'Please manually reconnect to your WiFi' });
        fetchCurrentWiFi();  // Refresh WiFi status
      }
    } else if (data.type === 'download_progress') {
      setDownloadProgress(prev => ({
        ...prev,
        [data.serial]: {
          filename: data.filename,
          current: data.current_file,
          total: data.total_files,
          percent: data.percent
        }
      }));
    } else if (data.type === 'download_complete') {
      setDownloadProgress(prev => {
        const newProgress = { ...prev };
        delete newProgress[data.serial];
        return newProgress;
      });
      fetchDownloadedFiles();
      fetchCurrentWiFi();  // Refresh WiFi status after download
      setMessage({ type: 'success', text: 'Download complete! WiFi has been switched back.' });
      setTimeout(() => setMessage(null), 5000);
    } else if (data.type === 'download_error') {
      setDownloadProgress(prev => {
        const newProgress = { ...prev };
        delete newProgress[data.serial];
        return newProgress;
      });
      setMessage({ type: 'error', text: data.error || 'Download failed' });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const fetchDownloadedFiles = async () => {
    try {
      const response = await axios.get(`${apiUrl}/api/downloads/list`);
      setDownloadedFiles(response.data.files);
    } catch (error) {
      console.error('Failed to fetch downloaded files:', error);
    }
  };

  const fetchCurrentWiFi = async () => {
    try {
      const response = await axios.get(`${apiUrl}/api/wifi/current`);
      setCurrentWiFi(response.data.ssid);
    } catch (error) {
      console.error('Failed to fetch current WiFi:', error);
      setCurrentWiFi(null);
    }
  };

  const handleDisconnectWiFi = async () => {
    setMessage({ type: 'info', text: 'Disconnecting from WiFi...' });

    try {
      await axios.post(`${apiUrl}/api/wifi/disconnect`);
      setMessage({
        type: 'success',
        text: '✅ Disconnected from GoPro WiFi! Please reconnect to your home/office WiFi to upload files.'
      });
      fetchCurrentWiFi();
      setTimeout(() => setMessage(null), 8000);
    } catch (error) {
      console.error('Disconnect failed:', error);
      setMessage({
        type: 'error',
        text: `Failed to disconnect: ${error.response?.data?.detail || error.message}`
      });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleEnableWiFi = async () => {
    if (connectedCameras.length === 0) {
      setMessage({ type: 'error', text: 'No cameras connected! Connect cameras first in Camera Management tab.' });
      setTimeout(() => setMessage(null), 5000);
      return;
    }

    setMessage({ type: 'info', text: `Enabling WiFi on ${connectedCameras.length} camera(s)...` });

    try {
      console.log('Enabling WiFi on cameras:', connectedCameras.map(c => c.serial));

      const response = await axios.post(`${apiUrl}/api/wifi/enable-all`, null, {
        timeout: 30000
      });

      console.log('WiFi enable results:', response.data.results);

      const results = response.data.results;
      const successCount = Object.values(results).filter(r => r).length;
      const totalCount = Object.keys(results).length;

      if (successCount === 0) {
        setMessage({
          type: 'error',
          text: 'Failed to enable WiFi on any camera. Check terminal logs.'
        });
        setTimeout(() => setMessage(null), 5000);
        return;
      }

      setMessage({
        type: 'success',
        text: `WiFi enabled on ${successCount}/${totalCount} cameras! Waiting 20 seconds for WiFi to stabilize...`
      });

      // Countdown timer
      let countdown = 20;
      const countdownInterval = setInterval(() => {
        countdown--;
        if (countdown > 0) {
          setMessage({
            type: 'info',
            text: `WiFi stabilizing... ${countdown} seconds remaining`
          });
        }
      }, 1000);

      // Wait for WiFi to be ready
      setTimeout(() => {
        clearInterval(countdownInterval);
        setMessage({
          type: 'success',
          text: '✅ WiFi is ready! You can now download files from cameras.'
        });
        setTimeout(() => setMessage(null), 5000);
      }, 20000);

    } catch (error) {
      console.error('Enable WiFi error:', error);
      setMessage({
        type: 'error',
        text: `Failed to enable WiFi: ${error.response?.data?.detail || error.message}`
      });
      setTimeout(() => setMessage(null), 5000);
    }
  };

  const handleDownloadFromCamera = async (serial) => {
    const camera = cameras.find(c => c.serial === serial);
    if (!camera) {
      setMessage({ type: 'error', text: 'Camera not found' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    setDownloading(true);
    setDownloadProgress(prev => ({
      ...prev,
      [serial]: { filename: 'Initializing...', current: 0, total: 0, percent: 0 }
    }));

    setMessage({
      type: 'info',
      text: `Connecting to ${camera.name || `GoPro ${serial}`} WiFi...`
    });

    try {
      console.log(`Starting download from camera ${serial}`, camera);

      const response = await axios.post(`${apiUrl}/api/download/${serial}`, null, {
        timeout: 300000 // 5 minute timeout for large files
      });

      console.log('Download response:', response.data);

      setMessage({
        type: 'success',
        text: `✅ Downloaded ${response.data.files_count} file(s) from ${camera.name || `GoPro ${serial}`}!`
      });

      // Clear progress
      setDownloadProgress(prev => {
        const newProgress = { ...prev };
        delete newProgress[serial];
        return newProgress;
      });

      fetchDownloadedFiles();
      setTimeout(() => setMessage(null), 5000);

    } catch (error) {
      console.error('Download error:', error);

      // Clear progress
      setDownloadProgress(prev => {
        const newProgress = { ...prev };
        delete newProgress[serial];
        return newProgress;
      });

      setMessage({
        type: 'error',
        text: error.response?.data?.detail || `Download failed: ${error.message}. Check terminal logs.`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setDownloading(false);
    }
  };

  const handleTestS3Backend = async () => {
    if (!uploadConfig.backend_url) {
      setMessage({ type: 'error', text: 'Please enter Backend URL first!' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    setMessage({ type: 'info', text: 'Testing S3 backend connection...' });

    try {
      const response = await axios.post(`${apiUrl}/api/test-s3-backend`, {
        backend_url: uploadConfig.backend_url,
        api_key: uploadConfig.api_key
      });

      if (response.data.success) {
        setMessage({
          type: 'success',
          text: `✅ ${response.data.message}\nURL: ${response.data.url}`
        });
      } else {
        setMessage({
          type: 'error',
          text: `❌ ${response.data.error}\nURL: ${response.data.url}`
        });
      }
      setTimeout(() => setMessage(null), 8000);

    } catch (error) {
      setMessage({
        type: 'error',
        text: `Connection test failed: ${error.response?.data?.error || error.message}`
      });
      setTimeout(() => setMessage(null), 8000);
    }
  };

  const handleUploadFile = async (file) => {
    if (!uploadConfig.backend_url || !uploadConfig.api_key) {
      setMessage({ type: 'error', text: 'Please configure S3 upload settings first!' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    setUploadingFiles(prev => new Set(prev).add(file.path));
    setMessage({ type: 'info', text: `Uploading ${file.name}...` });

    try {
      await axios.post(`${apiUrl}/api/upload`, {
        file_path: file.path,
        serial: file.serial,
        backend_url: uploadConfig.backend_url,
        api_key: uploadConfig.api_key
      });

      setMessage({ type: 'success', text: `${file.name} uploaded successfully!` });
      setTimeout(() => setMessage(null), 5000);

    } catch (error) {
      setMessage({
        type: 'error',
        text: `Failed to upload ${file.name}: ${error.response?.data?.detail || error.message}`
      });
      setTimeout(() => setMessage(null), 5000);
    } finally {
      setUploadingFiles(prev => {
        const newSet = new Set(prev);
        newSet.delete(file.path);
        return newSet;
      });
    }
  };

  const handleUploadCameraBulk = async (serial, folderName) => {
    if (!uploadConfig.backend_url || !uploadConfig.api_key) {
      setMessage({ type: 'error', text: 'Please configure S3 upload settings first!' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    setMessage({ type: 'info', text: `Creating ZIP for ${folderName} and uploading to S3...` });

    try {
      const response = await axios.post(`${apiUrl}/api/upload-camera-bulk/${serial}`, {
        backend_url: uploadConfig.backend_url,
        api_key: uploadConfig.api_key
      });

      const uploads = response.data.uploads;

      if (uploads && uploads.length > 0) {
        const firstUpload = uploads[0];
        const { zip_url, zip_filename, zip_size_mb, files_count } = firstUpload;

        // Add to uploaded ZIPs list
        setUploadedZips(prev => [{
          zip_url,
          zip_filename,
          zip_size_mb,
          files_count,
          folder_name: folderName,
          uploaded_at: new Date().toLocaleString()
        }, ...prev]);

        setMessage({
          type: 'success',
          text: (
            <div>
              <div style={{ marginBottom: '0.5rem' }}>
                🎉 Success! {files_count} files zipped and uploaded ({zip_size_mb} MB)
              </div>
              <div style={{ marginTop: '0.5rem' }}>
                <strong>Download ZIP:</strong>
                <br />
                <a
                  href={zip_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: '#667eea', textDecoration: 'underline', wordBreak: 'break-all' }}
                >
                  {zip_filename}
                </a>
              </div>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(zip_url);
                  alert('ZIP URL copied to clipboard!');
                }}
                style={{
                  marginTop: '0.75rem',
                  padding: '0.5rem 1rem',
                  background: '#667eea',
                  color: 'white',
                  border: 'none',
                  borderRadius: '6px',
                  cursor: 'pointer'
                }}
              >
                📋 Copy ZIP URL
              </button>
            </div>
          )
        });
      }

    } catch (error) {
      console.error('Bulk upload failed:', error);
      setMessage({
        type: 'error',
        text: `Bulk upload failed: ${error.response?.data?.detail || error.message}`
      });
      setTimeout(() => setMessage(null), 8000);
    }
  };

  const handleUploadAll = async () => {
    if (!uploadConfig.backend_url || !uploadConfig.api_key) {
      setMessage({ type: 'error', text: 'Please configure S3 upload settings first!' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    const totalFiles = downloadedFiles.length;
    setMessage({ type: 'info', text: `Starting bulk upload of ${totalFiles} file(s)...` });

    let successCount = 0;
    let failCount = 0;

    // Upload all files sequentially
    for (let i = 0; i < downloadedFiles.length; i++) {
      const file = downloadedFiles[i];
      const currentFileNum = i + 1;

      // Update upload progress
      setUploadProgress({
        current: currentFileNum,
        total: totalFiles,
        filename: file.name,
        percent: Math.round((currentFileNum / totalFiles) * 100)
      });

      setUploadingFiles(prev => new Set(prev).add(file.path));
      setMessage({
        type: 'info',
        text: `Uploading ${currentFileNum}/${totalFiles}: ${file.name}...`
      });

      try {
        await axios.post(`${apiUrl}/api/upload`, {
          file_path: file.path,
          serial: file.serial,
          backend_url: uploadConfig.backend_url,
          api_key: uploadConfig.api_key
        });

        successCount++;
        console.log(`✓ Uploaded: ${file.name}`);

      } catch (error) {
        failCount++;
        console.error(`✗ Failed to upload ${file.name}:`, error);
      } finally {
        setUploadingFiles(prev => {
          const newSet = new Set(prev);
          newSet.delete(file.path);
          return newSet;
        });
      }
    }

    // Clear upload progress
    setUploadProgress(null);

    // Show upload results
    if (failCount === 0) {
      setMessage({
        type: 'success',
        text: `✅ Successfully uploaded all ${successCount} files! Creating ZIP...`
      });
    } else {
      setMessage({
        type: 'warning',
        text: `Uploaded ${successCount}/${totalFiles} files. ${failCount} failed. Creating ZIP of uploaded files...`
      });
    }

    // Create ZIP and upload to S3
    try {
      setMessage({ type: 'info', text: '📦 Creating ZIP file and uploading to S3...' });

      const allFilePaths = downloadedFiles.map(f => f.path);

      const zipResponse = await axios.post(`${apiUrl}/api/create-zip`, {
        file_paths: allFilePaths,
        backend_url: uploadConfig.backend_url,
        api_key: uploadConfig.api_key
      });

      const { zip_url, zip_filename, zip_size_mb, files_count } = zipResponse.data;

      setMessage({
        type: 'success',
        text: (
          <div>
            <div style={{ marginBottom: '0.5rem' }}>
              🎉 Success! {files_count} files uploaded and zipped ({zip_size_mb} MB)
            </div>
            <div style={{ marginTop: '0.5rem' }}>
              <strong>Download ZIP:</strong>
              <br />
              <a
                href={zip_url}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: '#667eea', textDecoration: 'underline', wordBreak: 'break-all' }}
              >
                {zip_filename}
              </a>
            </div>
            <button
              onClick={() => {
                navigator.clipboard.writeText(zip_url);
                alert('ZIP URL copied to clipboard!');
              }}
              style={{
                marginTop: '0.75rem',
                padding: '0.5rem 1rem',
                background: '#667eea',
                color: 'white',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer'
              }}
            >
              📋 Copy ZIP URL
            </button>
          </div>
        )
      });

      console.log('ZIP URL:', zip_url);

    } catch (error) {
      console.error('ZIP creation failed:', error);
      setMessage({
        type: 'error',
        text: `Files uploaded but ZIP creation failed: ${error.response?.data?.detail || error.message}`
      });
      setTimeout(() => setMessage(null), 8000);
    }
  };

  const formatFileSize = (bytes) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
  };

  // Group files by folder (which includes date and camera name)
  const groupedFiles = downloadedFiles.reduce((acc, file) => {
    const folderKey = file.folder || `GoPro_${file.serial}`; // Use folder if available, fallback to serial
    if (!acc[folderKey]) {
      acc[folderKey] = {
        serial: file.serial,
        files: []
      };
    }
    acc[folderKey].files.push(file);
    return acc;
  }, {});

  return (
    <div className="download-upload">
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      {/* WiFi Status Warning */}
      {currentWiFi && cameras.some(cam => currentWiFi === cam.wifi_ssid || cam.wifi_ssid.includes(currentWiFi)) && (
        <div className="card" style={{ background: '#fff3cd', borderColor: '#ffc107' }}>
          <h2 style={{ color: '#856404' }}>⚠️ WiFi Connection Warning</h2>
          <div className="alert alert-warning" style={{ background: 'transparent', border: 'none', padding: 0 }}>
            <p style={{ marginBottom: '1rem', color: '#856404' }}>
              <strong>You are currently connected to GoPro WiFi: {currentWiFi}</strong>
            </p>
            <p style={{ marginBottom: '1rem', color: '#856404' }}>
              GoPro WiFi has no internet connectivity. You must disconnect and connect to your home/office WiFi before uploading files to S3.
            </p>
            <button
              className="btn"
              onClick={handleDisconnectWiFi}
              style={{
                background: '#ffc107',
                color: '#000',
                border: 'none',
                fontWeight: 'bold'
              }}
            >
              📡 Disconnect from GoPro WiFi
            </button>
            <p style={{ marginTop: '1rem', fontSize: '0.9rem', color: '#856404' }}>
              After disconnecting, reconnect to your home/office WiFi manually, then return here to upload files.
            </p>
          </div>
        </div>
      )}

      {/* Current WiFi Display */}
      <div className="card">
        <h2>WiFi Status</h2>
        <div style={{ padding: '1rem', background: '#f8f9fa', borderRadius: '6px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <strong>Current WiFi:</strong>{' '}
              <span style={{
                color: currentWiFi && cameras.some(cam => currentWiFi === cam.wifi_ssid || cam.wifi_ssid.includes(currentWiFi)) ? '#dc3545' : '#28a745',
                fontWeight: 'bold'
              }}>
                {currentWiFi || 'Not connected'}
              </span>
            </div>
            <button
              className="btn btn-secondary btn-sm"
              onClick={fetchCurrentWiFi}
              style={{ fontSize: '0.875rem', padding: '0.4rem 0.8rem' }}
            >
              🔄 Refresh
            </button>
          </div>
          {currentWiFi && !cameras.some(cam => currentWiFi === cam.wifi_ssid || cam.wifi_ssid.includes(currentWiFi)) && (
            <div style={{ marginTop: '0.5rem', color: '#28a745', fontSize: '0.9rem' }}>
              ✅ Connected to internet WiFi - ready to upload files
            </div>
          )}
        </div>
      </div>

      {/* S3 Upload Configuration */}
      <div className="card">
        <h2>S3 Upload Configuration</h2>
        <div className="form-row">
          <div className="form-group">
            <label>Backend URL</label>
            <input
              type="text"
              value={uploadConfig.backend_url}
              onChange={(e) => setUploadConfig({ ...uploadConfig, backend_url: e.target.value })}
              placeholder="https://your-backend.com/api/upload-file"
            />
          </div>
          <div className="form-group">
            <label>API Key</label>
            <input
              type="password"
              value={uploadConfig.api_key}
              onChange={(e) => setUploadConfig({ ...uploadConfig, api_key: e.target.value })}
              placeholder="Your API key"
            />
          </div>
        </div>
        <button
          className="btn btn-secondary"
          onClick={handleTestS3Backend}
          style={{ marginTop: '1rem' }}
        >
          🔍 Test S3 Backend Connection
        </button>
      </div>

      {/* Download Controls */}
      <div className="card">
        <h2>Download from Cameras</h2>

        {connectedCameras.length === 0 ? (
          <div className="alert alert-info">
            No cameras connected. Please connect cameras in the Camera Management tab first.
          </div>
        ) : (
          <>
            <div className="alert alert-info">
              <strong>How it works:</strong>
              <ol style={{ margin: '0.5rem 0', paddingLeft: '1.5rem' }}>
                <li>Stop all recordings first (Recording Control tab)</li>
                <li>Click "Enable WiFi on All Cameras" below (turns on WiFi AP via BLE)</li>
                <li>Wait 20 seconds for WiFi to stabilize</li>
                <li>Click "Download All Files" - this will automatically connect to that camera's WiFi and download files</li>
              </ol>
            </div>

            <div className="button-group">
              <button
                className="btn btn-secondary"
                onClick={handleEnableWiFi}
              >
                Step 1: Enable WiFi on All Cameras
              </button>
            </div>

            <div className="cameras-download-grid">
              {connectedCameras.map((camera) => (
                <div key={camera.serial} className="download-card">
                  <div className="download-card-header">
                    <h3>{camera.name || `GoPro ${camera.serial}`}</h3>
                    <span className="serial-badge">{camera.serial}</span>
                  </div>

                  {downloadProgress[camera.serial] ? (
                    <div className="download-progress-box">
                      <div className="progress-info">
                        <span>Downloading: {downloadProgress[camera.serial].filename}</span>
                        <span>
                          File {downloadProgress[camera.serial].current} of {downloadProgress[camera.serial].total}
                        </span>
                      </div>
                      <div className="progress-bar">
                        <div
                          className="progress-fill"
                          style={{ width: `${downloadProgress[camera.serial].percent}%` }}
                        ></div>
                      </div>
                      <div className="progress-percent">
                        {downloadProgress[camera.serial].percent}%
                      </div>
                    </div>
                  ) : (
                    <button
                      className="btn btn-primary"
                      onClick={() => handleDownloadFromCamera(camera.serial)}
                      disabled={downloading}
                    >
                      Step 2: Download All Files
                    </button>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Downloaded Files */}
      <div className="card">
        <div className="card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2>Downloaded Files ({downloadedFiles.length})</h2>
          {downloadedFiles.length > 0 && (
            <button
              className="btn btn-success"
              onClick={handleUploadAll}
              disabled={uploadingFiles.size > 0}
            >
              {uploadingFiles.size > 0 ? (
                <>
                  <span className="spinner-small"></span>
                  Uploading {uploadingFiles.size} file(s)...
                </>
              ) : (
                `Upload All ${downloadedFiles.length} Files to S3`
              )}
            </button>
          )}
        </div>

        {/* Upload Progress Bar */}
        {uploadProgress && (
          <div className="download-progress-box" style={{ margin: '1rem 0', background: '#e7f3ff', border: '2px solid #667eea' }}>
            <div className="progress-info">
              <span style={{ fontWeight: 'bold', color: '#667eea' }}>Uploading: {uploadProgress.filename}</span>
              <span style={{ color: '#667eea' }}>
                File {uploadProgress.current} of {uploadProgress.total}
              </span>
            </div>
            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{ width: `${uploadProgress.percent}%`, background: '#667eea' }}
              ></div>
            </div>
            <div className="progress-percent" style={{ color: '#667eea' }}>
              {uploadProgress.percent}%
            </div>
          </div>
        )}

        {downloadedFiles.length === 0 ? (
          <div className="empty-state">
            <p>No files downloaded yet. Download files from your cameras above.</p>
          </div>
        ) : (
          <div className="files-by-camera">
            {Object.entries(groupedFiles).map(([folderKey, groupData]) => (
              <div key={folderKey} className="camera-files-group">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                  <h3 className="camera-group-header">
                    {folderKey} ({groupData.files.length} files)
                  </h3>
                  <button
                    className="btn btn-success"
                    onClick={() => handleUploadCameraBulk(groupData.serial, folderKey)}
                    style={{ fontSize: '0.9rem', padding: '0.5rem 1rem' }}
                  >
                    Upload All to S3 as ZIP
                  </button>
                </div>

                <div className="files-list">
                  {groupData.files.map((file, idx) => (
                    <div key={idx} className="file-item">
                      <div className="file-info">
                        <div className="file-name">{file.name}</div>
                        <div className="file-size">{formatFileSize(file.size)}</div>
                      </div>

                      <button
                        className="btn btn-success btn-sm"
                        onClick={() => handleUploadFile(file)}
                        disabled={uploadingFiles.has(file.path)}
                      >
                        {uploadingFiles.has(file.path) ? (
                          <>
                            <span className="spinner-small"></span>
                            Uploading...
                          </>
                        ) : (
                          'Upload to S3'
                        )}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Uploaded ZIPs - S3 URLs */}
      {uploadedZips.length > 0 && (
        <div className="card" style={{ background: '#f0f9ff', borderColor: '#667eea' }}>
          <h2 style={{ color: '#667eea' }}>📦 Uploaded ZIP Files ({uploadedZips.length})</h2>
          <p style={{ color: '#666', marginBottom: '1rem' }}>
            All your uploaded ZIPs are listed below. URLs remain accessible for easy sharing.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {uploadedZips.map((zip, idx) => (
              <div
                key={idx}
                style={{
                  background: 'white',
                  padding: '1rem',
                  borderRadius: '8px',
                  border: '2px solid #667eea',
                  boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.75rem' }}>
                  <div style={{ flex: 1 }}>
                    <h3 style={{ margin: 0, color: '#333', fontSize: '1.1rem' }}>
                      {zip.folder_name}
                    </h3>
                    <div style={{ color: '#666', fontSize: '0.9rem', marginTop: '0.25rem' }}>
                      {zip.files_count} files • {zip.zip_size_mb} MB • Uploaded: {zip.uploaded_at}
                    </div>
                  </div>
                </div>

                <div style={{
                  background: '#f8f9fa',
                  padding: '0.75rem',
                  borderRadius: '6px',
                  marginBottom: '0.75rem',
                  fontFamily: 'monospace',
                  fontSize: '0.85rem',
                  wordBreak: 'break-all',
                  color: '#495057'
                }}>
                  {zip.zip_url}
                </div>

                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(zip.zip_url);
                      alert('ZIP URL copied to clipboard!');
                    }}
                    style={{
                      padding: '0.5rem 1rem',
                      background: '#667eea',
                      color: 'white',
                      border: 'none',
                      borderRadius: '6px',
                      cursor: 'pointer',
                      fontSize: '0.9rem',
                      fontWeight: '500'
                    }}
                  >
                    📋 Copy URL
                  </button>
                  <a
                    href={zip.zip_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      padding: '0.5rem 1rem',
                      background: '#28a745',
                      color: 'white',
                      border: 'none',
                      borderRadius: '6px',
                      cursor: 'pointer',
                      fontSize: '0.9rem',
                      fontWeight: '500',
                      textDecoration: 'none',
                      display: 'inline-block'
                    }}
                  >
                    ⬇️ Download ZIP
                  </a>
                </div>
              </div>
            ))}
          </div>

          <button
            onClick={() => {
              if (window.confirm('Clear all uploaded ZIP history? (URLs will still work, this just clears the list)')) {
                setUploadedZips([]);
              }
            }}
            style={{
              marginTop: '1rem',
              padding: '0.5rem 1rem',
              background: '#dc3545',
              color: 'white',
              border: 'none',
              borderRadius: '6px',
              cursor: 'pointer',
              fontSize: '0.9rem'
            }}
          >
            🗑️ Clear History
          </button>
        </div>
      )}

      {/* Instructions */}
      <div className="card">
        <h2>Download & Upload Workflow</h2>
        <ol className="instructions-list">
          <li><strong>Stop all recordings</strong> - Go to Recording Control tab and stop recording</li>
          <li><strong>Enable WiFi</strong> - Click "Step 1: Enable WiFi on All Cameras" and wait 20 seconds</li>
          <li><strong>Download files</strong> - Click "Step 2: Download All Files" for each camera
            <ul style={{ marginTop: '0.5rem', color: '#888' }}>
              <li>Your Mac will automatically disconnect from current WiFi</li>
              <li>Connect to camera's WiFi network (e.g., GP25471874)</li>
              <li>Download all files in newest-first order</li>
              <li>You'll see real-time progress for each file</li>
            </ul>
          </li>
          <li><strong>Upload to S3</strong> - Configure S3 settings at the top, then:
            <ul style={{ marginTop: '0.5rem', color: '#888' }}>
              <li>Click "Upload All to S3 as ZIP" for a camera to create a dated ZIP (e.g., 2026-02-18_GoPro8881.zip)</li>
              <li>Or click "Upload to S3" next to individual files</li>
            </ul>
          </li>
          <li><strong>Local files</strong> - Files are organized by date and camera: gopro_downloads/YYYY-MM-DD_GoPro[serial]/</li>
        </ol>
      </div>
    </div>
  );
}

export default DownloadUpload;
