import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './DownloadUpload.css';

function DownloadUpload({ cameras, apiUrl, downloadWsMessage, activeShoot }) {
  const [downloadedFiles, setDownloadedFiles] = useState([]);
  const [downloading, setDownloading] = useState(false);
  const [uploadConfig, setUploadConfig] = useState({
    backend_url: '',
    api_key: ''
  });
  const [uploadingFiles, setUploadingFiles] = useState(new Set());
  const [message, setMessage] = useState(null);
  const [downloadProgress, setDownloadProgress] = useState({});
  const [uploadProgress, setUploadProgress] = useState(null); // { current: 1, total: 8, filename: "file.mp4" }
  const [currentWiFi, setCurrentWiFi] = useState(null);
  const [wifiInfo, setWifiInfo] = useState({}); // Full wifi status {ssid, ip, on_gopro, network_type, display_name}
  const [uploadedZips, setUploadedZips] = useState([]); // Store all uploaded ZIP URLs
  const [maxFiles, setMaxFiles] = useState(''); // Number of files to download (empty = all files)
  const [bulkDownloadProgress, setBulkDownloadProgress] = useState(null); // { currentCamera: 2, totalCameras: 4, cameraName: "GoPro 2152" }

  const connectedCameras = cameras.filter(cam => cam.connected);

  useEffect(() => {
    fetchDownloadedFiles();
    fetchCurrentWiFi();

    // Refresh WiFi status every 5 seconds
    const wifiInterval = setInterval(fetchCurrentWiFi, 5000);

    return () => {
      clearInterval(wifiInterval);
    };
  }, []);

  // Handle download WS messages forwarded from App.js
  useEffect(() => {
    if (downloadWsMessage) {
      handleWebSocketMessage(downloadWsMessage);
    }
  }, [downloadWsMessage]);

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
      setWifiInfo(response.data);
      // Use display_name which works on macOS 26+ (where SSID is hidden)
      setCurrentWiFi(response.data.display_name || response.data.ssid);
    } catch (error) {
      console.error('Failed to fetch current WiFi:', error);
      setCurrentWiFi(null);
      setWifiInfo({});
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

  const handleDownloadFromCamera = async (serial, shootName = null, takeNumber = null) => {
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
      if (maxFiles) {
        console.log(`Limiting download to last ${maxFiles} files`);
      }

      const params = {};
      if (maxFiles) params.max_files = parseInt(maxFiles);
      if (shootName) params.shoot_name = shootName;
      if (takeNumber !== null && takeNumber !== undefined) params.take_number = takeNumber;

      const response = await axios.post(`${apiUrl}/api/download/${serial}`, null, {
        params,
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

  const handleDownloadFromAllCameras = async () => {
    if (connectedCameras.length === 0) {
      setMessage({ type: 'error', text: 'No cameras connected! Connect cameras first.' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    setDownloading(true);
    const totalCameras = connectedCameras.length;
    let successCount = 0;
    let failCount = 0;

    setMessage({
      type: 'info',
      text: `Starting sequential download from ${totalCameras} camera(s)...`
    });

    // Download from each camera sequentially
    for (let i = 0; i < connectedCameras.length; i++) {
      const camera = connectedCameras[i];
      const currentCameraNum = i + 1;
      const cameraName = camera.name || `GoPro ${camera.serial}`;

      // Update bulk download progress
      setBulkDownloadProgress({
        currentCamera: currentCameraNum,
        totalCameras: totalCameras,
        cameraName: cameraName,
        serial: camera.serial
      });

      // Set individual camera progress
      setDownloadProgress(prev => ({
        ...prev,
        [camera.serial]: { filename: 'Initializing...', current: 0, total: 0, percent: 0 }
      }));

      setMessage({
        type: 'info',
        text: `📥 [${currentCameraNum}/${totalCameras}] Connecting to ${cameraName} WiFi...`
      });

      try {
        console.log(`[${currentCameraNum}/${totalCameras}] Starting download from camera ${camera.serial}`);
        if (maxFiles) {
          console.log(`Limiting download to last ${maxFiles} files`);
        }

        const dlParams = {};
        if (maxFiles) dlParams.max_files = parseInt(maxFiles);
        // Pass shoot/take info when an active shoot has completed takes
        if (activeShoot && activeShoot.takes && activeShoot.takes.length > 0) {
          const latestTake = activeShoot.takes[activeShoot.takes.length - 1];
          if (latestTake.stopped_at) {
            dlParams.shoot_name = activeShoot.name;
            dlParams.take_number = latestTake.take_number;
          }
        }

        const response = await axios.post(`${apiUrl}/api/download/${camera.serial}`, null, {
          params: dlParams,
          timeout: 300000 // 5 minute timeout for large files
        });

        console.log(`[${currentCameraNum}/${totalCameras}] Download response:`, response.data);

        setMessage({
          type: 'success',
          text: `✅ [${currentCameraNum}/${totalCameras}] Downloaded ${response.data.files_count} file(s) from ${cameraName}!`
        });

        successCount++;

        // Clear individual camera progress
        setDownloadProgress(prev => {
          const newProgress = { ...prev };
          delete newProgress[camera.serial];
          return newProgress;
        });

        // Wait 2 seconds before moving to next camera
        if (i < connectedCameras.length - 1) {
          await new Promise(resolve => setTimeout(resolve, 2000));
        }

      } catch (error) {
        console.error(`[${currentCameraNum}/${totalCameras}] Download error:`, error);
        failCount++;

        // Clear individual camera progress
        setDownloadProgress(prev => {
          const newProgress = { ...prev };
          delete newProgress[camera.serial];
          return newProgress;
        });

        setMessage({
          type: 'error',
          text: `❌ [${currentCameraNum}/${totalCameras}] Failed to download from ${cameraName}: ${error.response?.data?.detail || error.message}. Continuing to next camera...`
        });

        // Wait 2 seconds before moving to next camera even on error
        if (i < connectedCameras.length - 1) {
          await new Promise(resolve => setTimeout(resolve, 2000));
        }
      }
    }

    // Clear bulk download progress
    setBulkDownloadProgress(null);
    setDownloading(false);

    // Refresh downloaded files list
    fetchDownloadedFiles();

    // Show final results
    if (failCount === 0) {
      setMessage({
        type: 'success',
        text: `🎉 Successfully downloaded from all ${successCount} camera(s)!`
      });
    } else {
      setMessage({
        type: 'warning',
        text: `⚠️ Download complete: ${successCount} succeeded, ${failCount} failed out of ${totalCameras} cameras.`
      });
    }

    setTimeout(() => setMessage(null), 8000);
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
              <div className="msg-success-header">
                🎉 Success! {files_count} files zipped and uploaded ({zip_size_mb} MB)
              </div>
              <div className="msg-zip-link-section">
                <strong>Download ZIP:</strong>
                <br />
                <a
                  href={zip_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="msg-zip-link"
                >
                  {zip_filename}
                </a>
              </div>
              <button
                className="btn-copy-zip-url"
                onClick={() => {
                  navigator.clipboard.writeText(zip_url);
                  alert('ZIP URL copied to clipboard!');
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
            <div className="msg-success-header">
              🎉 Success! {files_count} files uploaded and zipped ({zip_size_mb} MB)
            </div>
            <div className="msg-zip-link-section">
              <strong>Download ZIP:</strong>
              <br />
              <a
                href={zip_url}
                target="_blank"
                rel="noopener noreferrer"
                className="msg-zip-link"
              >
                {zip_filename}
              </a>
            </div>
            <button
              className="btn-copy-zip-url"
              onClick={() => {
                navigator.clipboard.writeText(zip_url);
                alert('ZIP URL copied to clipboard!');
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

  // Group files by folder — use shoot/take hierarchy when available
  const groupedFiles = downloadedFiles.reduce((acc, file) => {
    let folderKey;
    if (file.shoot_name && file.take_folder) {
      folderKey = `${file.shoot_name}/${file.take_folder}/GoPro${file.serial}`;
    } else {
      folderKey = file.folder || `GoPro_${file.serial}`;
    }
    if (!acc[folderKey]) {
      acc[folderKey] = {
        serial: file.serial,
        files: [],
        shoot_name: file.shoot_name || null,
        take_number: file.take_number !== undefined ? file.take_number : null
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
      {wifiInfo.on_gopro && (
        <div className="card wifi-warning-card">
          <h2>⚠️ WiFi Connection Warning</h2>
          <div className="alert alert-warning wifi-warning-content">
            <p className="wifi-warning-text">
              <strong>You are currently connected to GoPro WiFi: {currentWiFi}</strong>
            </p>
            <p className="wifi-warning-text">
              GoPro WiFi has no internet connectivity. You must disconnect and connect to your home/office WiFi before uploading files to S3.
            </p>
            <button
              className="btn btn-wifi-disconnect"
              onClick={handleDisconnectWiFi}
            >
              📡 Disconnect from GoPro WiFi
            </button>
            <p className="wifi-warning-footer">
              After disconnecting, reconnect to your home/office WiFi manually, then return here to upload files.
            </p>
          </div>
        </div>
      )}

      {/* Current WiFi Display */}
      <div className="card">
        <h2>WiFi Status</h2>
        <div className="wifi-status-box">
          <div className="wifi-status-row">
            <div>
              <strong>Current WiFi:</strong>{' '}
              <span className={
                wifiInfo.on_gopro ? 'wifi-name--gopro' :
                wifiInfo.network_type === 'internet' ? 'wifi-name--internet' :
                'wifi-name--disconnected'
              }>
                {currentWiFi || 'Not connected'}
              </span>
            </div>
            <button
              className="btn btn-secondary btn-sm btn-refresh"
              onClick={fetchCurrentWiFi}
            >
              Refresh
            </button>
          </div>
          {wifiInfo.network_type === 'internet' && (
            <div className="wifi-hint--internet">
              Connected to internet WiFi - ready to upload files
            </div>
          )}
          {wifiInfo.on_gopro && (
            <div className="wifi-hint--gopro">
              Connected to GoPro WiFi - no internet access. Disconnect to upload files.
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
          className="btn btn-secondary btn-test-s3"
          onClick={handleTestS3Backend}
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
              <ol className="how-it-works-list">
                <li>Stop all recordings first (Recording Control tab)</li>
                <li>Click "Enable WiFi on All Cameras" below (turns on WiFi AP via BLE)</li>
                <li>Wait 20 seconds for WiFi to stabilize</li>
                <li>Click "Download All Files" - this will automatically connect to that camera's WiFi and download files</li>
              </ol>
            </div>

            {/* Download Limit Input */}
            <div className="form-group download-limit-group">
              <label className="download-limit-label">
                Number of Files to Download (Optional)
              </label>
              <input
                type="number"
                min="1"
                value={maxFiles}
                onChange={(e) => setMaxFiles(e.target.value)}
                placeholder="Leave empty to download all files"
                className="download-limit-input"
              />
              <small className="download-limit-hint">
                Leave empty to download all files, or enter a number (e.g., 5) to download only the last N files (newest first)
              </small>
            </div>

            <div className="button-group">
              <button
                className="btn btn-secondary"
                onClick={handleEnableWiFi}
              >
                Step 1: Enable WiFi on All Cameras
              </button>
            </div>

            {/* Bulk Download Progress */}
            {bulkDownloadProgress && (
              <div className="card bulk-progress-card">
                <h3 className="bulk-progress-title">
                  📥 Bulk Download Progress
                </h3>
                <div className="bulk-progress-camera">
                  Camera {bulkDownloadProgress.currentCamera} of {bulkDownloadProgress.totalCameras}: {bulkDownloadProgress.cameraName}
                </div>
                <div className="progress-bar bulk-progress-bar">
                  <div
                    className="progress-fill"
                    style={{ width: `${(bulkDownloadProgress.currentCamera / bulkDownloadProgress.totalCameras) * 100}%` }}
                  ></div>
                </div>
                {downloadProgress[bulkDownloadProgress.serial] && (
                  <div className="bulk-progress-detail">
                    <div className="bulk-progress-detail-text">
                      Downloading: {downloadProgress[bulkDownloadProgress.serial].filename}
                    </div>
                    <div className="bulk-progress-detail-text">
                      File {downloadProgress[bulkDownloadProgress.serial].current} of {downloadProgress[bulkDownloadProgress.serial].total}
                    </div>
                    <div className="progress-bar">
                      <div
                        className="progress-fill"
                        style={{ width: `${downloadProgress[bulkDownloadProgress.serial].percent}%` }}
                      ></div>
                    </div>
                    <div className="bulk-progress-percent">
                      {downloadProgress[bulkDownloadProgress.serial].percent}%
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Download from All Cameras Button */}
            <div className="download-all-wrapper">
              <button
                className="btn btn-primary btn-download-all"
                onClick={handleDownloadFromAllCameras}
                disabled={downloading}
              >
                {downloading ? (
                  <>
                    <span className="spinner-small"></span>
                    Downloading...
                  </>
                ) : (
                  `Step 2: Download from All ${connectedCameras.length} Cameras`
                )}
              </button>
              <small className="download-all-hint">
                This will download {maxFiles ? `last ${maxFiles} files` : 'all files'} from each camera sequentially
              </small>
            </div>

            <div className="section-divider">
              <strong>OR</strong> download from individual cameras below
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
                      Download {maxFiles ? `Last ${maxFiles}` : 'All'} Files
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
        <div className="card-header card-header-row">
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
          <div className="download-progress-box upload-progress-box">
            <div className="progress-info">
              <span>Uploading: {uploadProgress.filename}</span>
              <span>
                File {uploadProgress.current} of {uploadProgress.total}
              </span>
            </div>
            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{ width: `${uploadProgress.percent}%` }}
              ></div>
            </div>
            <div className="progress-percent">
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
                <div className="camera-group-row">
                  <h3 className="camera-group-header">
                    {folderKey} ({groupData.files.length} files)
                  </h3>
                  <button
                    className="btn btn-success btn-upload-zip"
                    onClick={() => handleUploadCameraBulk(groupData.serial, folderKey)}
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
        <div className="card uploaded-zips-card">
          <h2>📦 Uploaded ZIP Files ({uploadedZips.length})</h2>
          <p className="uploaded-zips-desc">
            All your uploaded ZIPs are listed below. URLs remain accessible for easy sharing.
          </p>

          <div className="uploaded-zips-list">
            {uploadedZips.map((zip, idx) => (
              <div key={idx} className="zip-card">
                <div className="zip-card-header">
                  <div className="zip-card-header-inner">
                    <h3 className="zip-card-title">
                      {zip.folder_name}
                    </h3>
                    <div className="zip-card-meta">
                      {zip.files_count} files • {zip.zip_size_mb} MB • Uploaded: {zip.uploaded_at}
                    </div>
                  </div>
                </div>

                <div className="zip-url-display">
                  {zip.zip_url}
                </div>

                <div className="zip-actions">
                  <button
                    className="btn-copy-url"
                    onClick={() => {
                      navigator.clipboard.writeText(zip.zip_url);
                      alert('ZIP URL copied to clipboard!');
                    }}
                  >
                    📋 Copy URL
                  </button>
                  <a
                    href={zip.zip_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-download-zip"
                  >
                    ⬇️ Download ZIP
                  </a>
                </div>
              </div>
            ))}
          </div>

          <button
            className="btn-clear-history"
            onClick={() => {
              if (window.confirm('Clear all uploaded ZIP history? (URLs will still work, this just clears the list)')) {
                setUploadedZips([]);
              }
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
          <li><strong>Download files</strong> - Optionally specify number of files to download (e.g., 5 for last 5 files), then:
            <ul className="instructions-sublist">
              <li><strong>Option A:</strong> Click "Step 2: Download from All Cameras" to download from all cameras sequentially (recommended)</li>
              <li><strong>Option B:</strong> Click "Download Files" for individual cameras if you only need specific cameras</li>
              <li>Your Mac will automatically connect to each camera's WiFi network</li>
              <li>Files are downloaded in newest-first order (all files or limited to last N files)</li>
              <li>You'll see real-time progress showing which camera and which file is downloading</li>
              <li>If one camera fails, the system continues to the next camera</li>
            </ul>
          </li>
          <li><strong>Upload to S3</strong> - Configure S3 settings at the top, then:
            <ul className="instructions-sublist">
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
