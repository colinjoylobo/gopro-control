import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './DownloadUpload.css';

function DownloadUpload({ cameras, apiUrl, downloadWsMessage, activeShoot, cohnStatus }) {
  const [downloadedFiles, setDownloadedFiles] = useState([]);
  const [downloading, setDownloading] = useState(false);
  const [uploadConfig, setUploadConfig] = useState({
    backend_url: '',
    api_key: ''
  });
  const [uploadingFiles, setUploadingFiles] = useState(new Set());
  const [message, setMessage] = useState(null);
  const [downloadProgress, setDownloadProgress] = useState({});
  const [uploadProgress, setUploadProgress] = useState(null);
  const [currentWiFi, setCurrentWiFi] = useState(null);
  const [wifiInfo, setWifiInfo] = useState({});
  const [uploadedZips, setUploadedZips] = useState([]);
  const [maxFiles, setMaxFiles] = useState('');
  const [bulkDownloadProgress, setBulkDownloadProgress] = useState(null);
  const [selectedTakeIdx, setSelectedTakeIdx] = useState('latest');
  // Browse/scan state
  const [browseFiles, setBrowseFiles] = useState({}); // { serial: { videos: [], others: [], total_files, total_size_human } }
  const [browsing, setBrowsing] = useState(null); // serial currently being browsed
  const [selectedFiles, setSelectedFiles] = useState({}); // { serial: Set of "dir/filename" }
  // Erase state
  const [eraseConfirm, setEraseConfirm] = useState(null); // serial awaiting confirmation
  const [eraseInput, setEraseInput] = useState('');
  const [erasing, setErasing] = useState(null); // serial currently being erased
  // Bulk erase state
  const [bulkEraseConfirm, setBulkEraseConfirm] = useState(false);
  const [bulkEraseInput, setBulkEraseInput] = useState('');
  const [bulkErasing, setBulkErasing] = useState(false);
  const [bulkEraseProgress, setBulkEraseProgress] = useState(null); // { current, total, serial }
  const [s3Expanded, setS3Expanded] = useState(() => localStorage.getItem('gopro_s3_expanded') === 'true');

  // Grid layout: 'auto' = smart (2 cols even, 3 cols odd), '2col', '3col'
  const [gridLayout, setGridLayout] = useState(() => localStorage.getItem('download_grid_layout') || 'auto');

  const handleGridLayout = (layout) => {
    setGridLayout(layout);
    localStorage.setItem('download_grid_layout', layout);
  };

  const getGridClass = () => {
    if (gridLayout === '2col') return 'grid-2col';
    if (gridLayout === '3col') return 'grid-3col';
    return cameras.length % 2 === 0 ? 'grid-2col' : 'grid-3col';
  };

  const connectedCameras = cameras.filter(cam => cam.connected);
  const sortedCameras = [...cameras].sort((a, b) => {
    if (a.connected && !b.connected) return -1;
    if (!a.connected && b.connected) return 1;
    return 0;
  });

  const allCohn = connectedCameras.length > 0 && connectedCameras.every(cam => {
    const cohn = (cohnStatus || {})[cam.serial];
    return cohn && cohn.provisioned && cohn.online;
  });

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

  // Persist S3 section expanded state
  useEffect(() => {
    localStorage.setItem('gopro_s3_expanded', s3Expanded);
  }, [s3Expanded]);

  const handleWebSocketMessage = (data) => {
    if (data.type === 'download_status') {
      // COHN: skip WiFi statuses, go straight to downloading
      if (data.status === 'downloading') {
        setMessage({ type: 'info', text: data.message || 'Downloading...' });
      } else if (data.status === 'connecting_wifi') {
        setMessage({ type: 'info', text: data.message || 'Connecting to camera WiFi...' });
      } else if (data.status === 'wifi_connected') {
        setMessage({ type: 'success', text: data.message || 'WiFi connected! Starting download...' });
      } else if (data.status === 'reconnecting_wifi') {
        setMessage({ type: 'info', text: data.message || 'Reconnecting to internet WiFi...' });
      } else if (data.status === 'wifi_restored') {
        setMessage({ type: 'success', text: data.message || 'WiFi restored! Ready to upload.' });
        fetchCurrentWiFi();
      } else if (data.status === 'wifi_manual_needed') {
        setMessage({ type: 'warning', text: data.message || 'Please manually reconnect to your WiFi' });
        fetchCurrentWiFi();
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
      fetchCurrentWiFi();
      const completeText = data.transport === 'cohn'
        ? 'Download complete!'
        : 'Download complete! WiFi has been switched back.';
      setMessage({ type: 'success', text: completeText });
      setTimeout(() => setMessage(null), 5000);
    } else if (data.type === 'download_error') {
      setDownloadProgress(prev => {
        const newProgress = { ...prev };
        delete newProgress[data.serial];
        return newProgress;
      });
      setMessage({ type: 'error', text: data.error || 'Download failed' });
      setTimeout(() => setMessage(null), 5000);
    } else if (data.type === 'browse_status') {
      if (data.status === 'scanning') {
        setMessage({ type: 'info', text: data.message || 'Scanning camera media...' });
      } else if (data.status === 'error') {
        setBrowsing(null);
        setMessage({ type: 'error', text: data.message || 'Browse failed' });
        setTimeout(() => setMessage(null), 5000);
      }
    } else if (data.type === 'browse_complete') {
      setBrowsing(null);
      setMessage({ type: 'success', text: `Found ${data.summary?.total_files || 0} files on camera ${data.serial}` });
      setTimeout(() => setMessage(null), 5000);
    } else if (data.type === 'sd_erased') {
      setErasing(null);
      if (data.success) {
        setMessage({ type: 'success', text: `SD card erased on camera ${data.serial}` });
        setBrowseFiles(prev => { const n = {...prev}; delete n[data.serial]; return n; });
      } else {
        setMessage({ type: 'error', text: `Failed to erase SD card on camera ${data.serial}` });
      }
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

  const getSelectedTake = () => {
    if (!activeShoot || !activeShoot.takes || activeShoot.takes.length === 0) return null;
    if (selectedTakeIdx === 'latest') {
      const completedTakes = activeShoot.takes.filter(t => t.stopped_at);
      return completedTakes.length > 0 ? completedTakes[completedTakes.length - 1] : null;
    }
    if (selectedTakeIdx === 'none') return null;
    return activeShoot.takes[parseInt(selectedTakeIdx)] || null;
  };

  const handleDownloadFromCamera = async (serial, shootName = null, takeNumber = null) => {
    const camera = cameras.find(c => c.serial === serial);
    if (!camera) {
      setMessage({ type: 'error', text: 'Camera not found' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    // Use selected take if no explicit shoot/take passed
    if (shootName === null && activeShoot) {
      const take = getSelectedTake();
      if (take && take.stopped_at) {
        shootName = activeShoot.name;
        takeNumber = take.take_number;
      }
    }

    setDownloading(true);
    setDownloadProgress(prev => ({
      ...prev,
      [serial]: { filename: 'Initializing...', current: 0, total: 0, percent: 0 }
    }));

    setMessage({
      type: 'info',
      text: `Starting download from ${camera.name || `GoPro ${serial}`}...`
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

  const handleDownloadFromAllCameras = async (overrideShootName = null, overrideTakeNumber = null) => {
    if (connectedCameras.length === 0) {
      setMessage({ type: 'error', text: 'No cameras connected! Connect cameras first.' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    setDownloading(true);
    const totalCameras = connectedCameras.length;

    // Build common params — use overrides if provided (avoids stale React state)
    const dlParams = {};
    if (maxFiles) dlParams.max_files = parseInt(maxFiles);
    if (overrideShootName && overrideTakeNumber !== null) {
      dlParams.shoot_name = overrideShootName;
      dlParams.take_number = overrideTakeNumber;
    } else if (activeShoot) {
      const take = getSelectedTake();
      if (take && take.stopped_at) {
        dlParams.shoot_name = activeShoot.name;
        dlParams.take_number = take.take_number;
      }
    }

    // Set initial progress for all cameras
    connectedCameras.forEach(camera => {
      setDownloadProgress(prev => ({
        ...prev,
        [camera.serial]: { filename: 'Initializing...', current: 0, total: 0, percent: 0 }
      }));
    });

    setBulkDownloadProgress({
      currentCamera: totalCameras,
      totalCameras: totalCameras,
      cameraName: `All ${totalCameras} cameras`,
      serial: null
    });

    setMessage({
      type: 'info',
      text: `Downloading from ${totalCameras} camera(s) in parallel via COHN...`
    });

    // Fire all downloads in parallel
    const downloadPromises = connectedCameras.map(camera =>
      axios.post(`${apiUrl}/api/download/${camera.serial}`, null, {
        params: dlParams,
        timeout: 300000
      }).then(response => ({ serial: camera.serial, response }))
       .catch(error => ({ serial: camera.serial, error }))
    );

    const results = await Promise.allSettled(downloadPromises);

    let successCount = 0;
    let failCount = 0;

    results.forEach(result => {
      const val = result.status === 'fulfilled' ? result.value : result.reason;
      const serial = val?.serial;
      if (serial) {
        setDownloadProgress(prev => {
          const newProgress = { ...prev };
          delete newProgress[serial];
          return newProgress;
        });
      }
      if (result.status === 'fulfilled' && !val.error) {
        successCount++;
      } else {
        failCount++;
      }
    });

    // Clear bulk download progress
    setBulkDownloadProgress(null);
    setDownloading(false);

    // Refresh downloaded files list
    fetchDownloadedFiles();

    // Show final results
    if (failCount === 0) {
      setMessage({
        type: 'success',
        text: `Successfully downloaded from all ${successCount} camera(s)!`
      });
    } else {
      setMessage({
        type: 'warning',
        text: `Download complete: ${successCount} succeeded, ${failCount} failed out of ${totalCameras} cameras.`
      });
    }

    setTimeout(() => setMessage(null), 8000);
  };

  const handleBrowseCamera = async (serial) => {
    setBrowsing(serial);
    setMessage({ type: 'info', text: `Scanning SD card on camera ${serial}...` });

    try {
      const response = await axios.post(`${apiUrl}/api/browse/${serial}`, null, { timeout: 120000 });
      const summary = response.data.summary;
      setBrowseFiles(prev => ({ ...prev, [serial]: summary }));
      // Initialize selected files for this camera
      setSelectedFiles(prev => ({ ...prev, [serial]: new Set() }));
    } catch (error) {
      setMessage({
        type: 'error',
        text: `Browse failed: ${error.response?.data?.detail || error.message}`
      });
      setTimeout(() => setMessage(null), 5000);
    } finally {
      setBrowsing(null);
    }
  };

  const handleDownloadLatest = async (serial) => {
    const camera = cameras.find(c => c.serial === serial);
    if (!camera) return;

    setDownloading(true);
    setDownloadProgress(prev => ({
      ...prev,
      [serial]: { filename: 'Fetching latest video...', current: 0, total: 0, percent: 0 }
    }));
    setMessage({ type: 'info', text: `Downloading latest video from ${camera.name || `GoPro ${serial}`}...` });

    try {
      const response = await axios.post(`${apiUrl}/api/download/${serial}/latest`, null, { timeout: 300000 });
      setMessage({
        type: 'success',
        text: `Downloaded ${response.data.files_count} file(s) from ${camera.name || `GoPro ${serial}`}!`
      });
      setDownloadProgress(prev => { const n = {...prev}; delete n[serial]; return n; });
      fetchDownloadedFiles();
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setDownloadProgress(prev => { const n = {...prev}; delete n[serial]; return n; });
      setMessage({
        type: 'error',
        text: error.response?.data?.detail || `Download latest failed: ${error.message}`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setDownloading(false);
    }
  };

  const handleDownloadSelected = async (serial) => {
    const selected = selectedFiles[serial];
    if (!selected || selected.size === 0) {
      setMessage({ type: 'error', text: 'No files selected' });
      setTimeout(() => setMessage(null), 3000);
      return;
    }

    const camera = cameras.find(c => c.serial === serial);
    if (!camera) return;

    const files = Array.from(selected).map(key => {
      const [directory, ...rest] = key.split('/');
      return { directory, filename: rest.join('/') };
    });

    setDownloading(true);
    setDownloadProgress(prev => ({
      ...prev,
      [serial]: { filename: 'Initializing...', current: 0, total: files.length, percent: 0 }
    }));
    setMessage({ type: 'info', text: `Downloading ${files.length} selected file(s) from ${camera.name || `GoPro ${serial}`}...` });

    try {
      const response = await axios.post(`${apiUrl}/api/download/${serial}/selected`, { files }, { timeout: 600000 });
      setMessage({
        type: 'success',
        text: `Downloaded ${response.data.files_count} file(s)!`
      });
      setDownloadProgress(prev => { const n = {...prev}; delete n[serial]; return n; });
      setSelectedFiles(prev => ({ ...prev, [serial]: new Set() }));
      fetchDownloadedFiles();
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setDownloadProgress(prev => { const n = {...prev}; delete n[serial]; return n; });
      setMessage({
        type: 'error',
        text: error.response?.data?.detail || `Selective download failed: ${error.message}`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setDownloading(false);
    }
  };

  const toggleFileSelection = (serial, directory, filename) => {
    const key = `${directory}/${filename}`;
    setSelectedFiles(prev => {
      const current = new Set(prev[serial] || []);
      if (current.has(key)) {
        current.delete(key);
      } else {
        current.add(key);
      }
      return { ...prev, [serial]: current };
    });
  };

  const toggleSelectAll = (serial) => {
    const browse = browseFiles[serial];
    if (!browse) return;
    const allFiles = [...(browse.videos || []), ...(browse.others || [])];
    const allKeys = allFiles.map(f => `${f.directory}/${f.filename}`);
    const current = selectedFiles[serial] || new Set();

    if (current.size === allKeys.length) {
      // Deselect all
      setSelectedFiles(prev => ({ ...prev, [serial]: new Set() }));
    } else {
      // Select all
      setSelectedFiles(prev => ({ ...prev, [serial]: new Set(allKeys) }));
    }
  };

  const handleEraseCamera = async (serial) => {
    setErasing(serial);
    setEraseConfirm(null);
    setEraseInput('');
    setMessage({ type: 'info', text: `Erasing SD card on camera ${serial}...` });

    try {
      await axios.post(`${apiUrl}/api/cameras/${serial}/erase-sd`, null, { timeout: 120000 });
      setMessage({ type: 'success', text: `SD card erased on camera ${serial}` });
      setBrowseFiles(prev => { const n = {...prev}; delete n[serial]; return n; });
      setTimeout(() => setMessage(null), 5000);
    } catch (error) {
      setMessage({
        type: 'error',
        text: error.response?.data?.detail || `Erase failed: ${error.message}`
      });
      setTimeout(() => setMessage(null), 8000);
    } finally {
      setErasing(null);
    }
  };

  const handleBulkErase = async () => {
    setBulkErasing(true);
    setBulkEraseConfirm(false);
    setBulkEraseInput('');
    const connected = connectedCameras;
    let successCount = 0;
    for (let i = 0; i < connected.length; i++) {
      const cam = connected[i];
      setBulkEraseProgress({ current: i + 1, total: connected.length, serial: cam.serial });
      setMessage({ type: 'info', text: `Erasing SD card ${i + 1}/${connected.length}: ${cam.name || `GoPro ${cam.serial}`}...` });
      try {
        await axios.post(`${apiUrl}/api/cameras/${cam.serial}/erase-sd`, null, { timeout: 120000 });
        successCount++;
        setBrowseFiles(prev => { const n = {...prev}; delete n[cam.serial]; return n; });
      } catch (error) {
        console.error(`Failed to erase ${cam.serial}:`, error);
      }
    }
    setBulkErasing(false);
    setBulkEraseProgress(null);
    if (successCount === connected.length) {
      setMessage({ type: 'success', text: `Erased SD cards on all ${successCount} cameras` });
    } else {
      setMessage({ type: 'warning', text: `Erased ${successCount}/${connected.length} cameras. Some failed.` });
    }
    setTimeout(() => setMessage(null), 5000);
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
      {!allCohn && wifiInfo.on_gopro && (
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
      {!allCohn && (
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
      )}

      {/* S3 Upload Configuration */}
      <div className="card">
        <div className="s3-header" onClick={() => setS3Expanded(!s3Expanded)} style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2>Cloud Upload (S3)</h2>
          <span style={{ color: '#888', fontSize: '0.85rem' }}>{s3Expanded ? '\u25BC' : '\u25B6'}</span>
        </div>
        {s3Expanded && (
          <div>
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
              Test S3 Backend Connection
            </button>
          </div>
        )}
      </div>

      {/* Download by Take */}
      {activeShoot && (
        <div className="card take-download-section">
          <h2>Download by Take — {activeShoot.name}</h2>
          {activeShoot.takes && activeShoot.takes.filter(t => t.stopped_at).length > 0 ? (
            <>
              {/* Download ALL takes button */}
              <div style={{ marginBottom: '1rem' }}>
                <button
                  className="btn btn-primary btn-download-all"
                  onClick={async () => {
                    setDownloading(true);
                    const completedTakes = activeShoot.takes.filter(t => t.stopped_at);
                    setMessage({ type: 'info', text: `Downloading all ${completedTakes.length} takes from "${activeShoot.name}"...` });
                    // Set progress for all cameras
                    const allCohn = Object.keys(cohnStatus || {});
                    allCohn.forEach(serial => {
                      setDownloadProgress(prev => ({ ...prev, [serial]: { filename: 'Initializing...', current: 0, total: 0, percent: 0 } }));
                    });
                    try {
                      const response = await axios.post(`${apiUrl}/api/download/shoot/${activeShoot.id}`, null, { timeout: 600000 });
                      setMessage({
                        type: 'success',
                        text: `Downloaded ${response.data.total_files} files across ${completedTakes.length} takes!`
                      });
                      // Clear progress for all cameras
                      allCohn.forEach(serial => {
                        setDownloadProgress(prev => { const n = {...prev}; delete n[serial]; return n; });
                      });
                      fetchDownloadedFiles();
                    } catch (error) {
                      setMessage({
                        type: 'error',
                        text: error.response?.data?.detail || `Bulk download failed: ${error.message}`
                      });
                      allCohn.forEach(serial => {
                        setDownloadProgress(prev => { const n = {...prev}; delete n[serial]; return n; });
                      });
                    } finally {
                      setDownloading(false);
                      setTimeout(() => setMessage(null), 8000);
                    }
                  }}
                  disabled={downloading}
                >
                  {downloading ? 'Downloading...' : `Download All ${activeShoot.takes.filter(t => t.stopped_at).length} Takes`}
                </button>
                <small style={{ display: 'block', marginTop: '0.25rem', color: 'var(--text-dimmed)' }}>
                  Downloads all takes from all cameras in parallel. One media list fetch per camera.
                </small>
              </div>
              <div className="take-download-list">
                {activeShoot.takes.filter(t => t.stopped_at).reverse().map(take => (
                  <div key={take.take_number} className="take-download-item">
                    <div className="take-download-info">
                      <span className="take-download-number">Take {take.take_number}</span>
                      {take.name && <span className="take-download-name">{take.name}</span>}
                      <span className="take-download-cameras">{take.cameras?.length || 0} cameras</span>
                    </div>
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() => handleDownloadFromAllCameras(activeShoot.name, take.take_number)}
                      disabled={downloading}
                    >
                      Download Take {take.take_number}
                    </button>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p style={{ color: 'var(--text-dimmed)', fontStyle: 'italic' }}>
              No completed takes yet. Record from the Dashboard tab to create takes.
            </p>
          )}
        </div>
      )}

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
                <li>Click "Download from All Cameras" — downloads happen in parallel via COHN (no WiFi switching needed)</li>
                <li>If a camera lacks COHN credentials, enable WiFi first using the button below, then download individually</li>
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

            {/* Take Selector — shown when active shoot has takes */}
            {activeShoot && activeShoot.takes && activeShoot.takes.length > 0 && (
              <div className="form-group take-selector-group">
                <label className="take-selector-label">
                  Download into Take Folder
                </label>
                <div className="take-selector-row">
                  <select
                    className="take-selector-select"
                    value={selectedTakeIdx}
                    onChange={(e) => setSelectedTakeIdx(e.target.value)}
                  >
                    <option value="latest">Latest Completed Take</option>
                    <option value="none">No Take (date-based folder)</option>
                    {activeShoot.takes.map((take, idx) => (
                      <option key={idx} value={idx}>
                        Take {take.take_number}
                        {take.stopped_at ? '' : ' (in progress)'}
                        {' — '}
                        {take.cameras?.length || 0} camera{(take.cameras?.length || 0) !== 1 ? 's' : ''}
                      </option>
                    ))}
                  </select>
                  <span className="take-selector-shoot-name">
                    Shoot: {activeShoot.name}
                  </span>
                </div>
                {selectedTakeIdx !== 'none' && (() => {
                  const take = selectedTakeIdx === 'latest' ? getSelectedTake() : activeShoot.takes[parseInt(selectedTakeIdx)];
                  if (take) {
                    return (
                      <small className="take-selector-hint">
                        Files will be saved to: {activeShoot.name}/Take_{String(take.take_number).padStart(2, '0')}/GoPro[serial]/
                        {!take.stopped_at && <strong> (take still in progress — stop recording first)</strong>}
                      </small>
                    );
                  }
                  return <small className="take-selector-hint">No completed takes yet. Stop recording to create a downloadable take.</small>;
                })()}
              </div>
            )}

            {!allCohn && (
              <div className="button-group">
                <button
                  className="btn btn-secondary"
                  onClick={handleEnableWiFi}
                >
                  Enable WiFi on All Cameras (non-COHN fallback)
                </button>
              </div>
            )}

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
                  `Download from All ${connectedCameras.length} Cameras`
                )}
              </button>
              <small className="download-all-hint">
                Downloads {maxFiles ? `last ${maxFiles} files` : 'all files'} from all cameras in parallel via COHN
              </small>
            </div>

            {/* Bulk Erase All SD Cards */}
            <div className="download-all-wrapper">
              {!bulkEraseConfirm && !bulkErasing && (
                <button
                  className="btn btn-danger btn-download-all"
                  onClick={() => { setBulkEraseConfirm(true); setBulkEraseInput(''); }}
                  disabled={downloading || bulkErasing}
                >
                  Erase All SD Cards ({connectedCameras.length} cameras)
                </button>
              )}

              {bulkEraseConfirm && (
                <div className="erase-confirm-box">
                  <p className="erase-confirm-text">
                    Type <strong>ERASE ALL</strong> to confirm erasing SD cards on {connectedCameras.length} connected camera(s). This cannot be undone.
                  </p>
                  <div className="erase-confirm-row">
                    <input
                      type="text"
                      value={bulkEraseInput}
                      onChange={(e) => setBulkEraseInput(e.target.value)}
                      placeholder="Type ERASE ALL"
                      className="erase-confirm-input"
                      autoFocus
                    />
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={handleBulkErase}
                      disabled={bulkEraseInput !== 'ERASE ALL'}
                    >
                      Confirm Erase All
                    </button>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => { setBulkEraseConfirm(false); setBulkEraseInput(''); }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {bulkEraseProgress && (
                <div className="card bulk-progress-card" style={{ borderColor: 'var(--danger)' }}>
                  <h3 className="bulk-progress-title" style={{ color: 'var(--danger)' }}>
                    Erasing SD Cards
                  </h3>
                  <div className="bulk-progress-camera">
                    Camera {bulkEraseProgress.current} of {bulkEraseProgress.total}: {bulkEraseProgress.serial}
                  </div>
                  <div className="progress-bar bulk-progress-bar">
                    <div
                      className="progress-fill"
                      style={{ width: `${(bulkEraseProgress.current / bulkEraseProgress.total) * 100}%`, background: 'var(--danger)' }}
                    ></div>
                  </div>
                </div>
              )}
            </div>

            <div className="section-divider">
              <strong>OR</strong> download from individual cameras below
            </div>

            <div className="grid-layout-bar">
              <span className="layout-label">Layout</span>
              <button className={`layout-btn ${gridLayout === 'auto' ? 'active' : ''}`} onClick={() => handleGridLayout('auto')}>Auto</button>
              <button className={`layout-btn ${gridLayout === '2col' ? 'active' : ''}`} onClick={() => handleGridLayout('2col')}>2-Col</button>
              <button className={`layout-btn ${gridLayout === '3col' ? 'active' : ''}`} onClick={() => handleGridLayout('3col')}>3-Col</button>
            </div>
            <div className={`cameras-download-grid ${getGridClass()}`}>
              {sortedCameras.map((camera) => (
                <div key={camera.serial} className={`download-card ${!camera.connected ? 'download-card-disconnected' : ''}`}>
                  <div className="download-card-header">
                    <h3>{camera.name || `GoPro ${camera.serial}`}</h3>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      {!camera.connected && (
                        <span className="not-connected-badge">Not Connected</span>
                      )}
                      <span className="serial-badge">{camera.serial}</span>
                    </div>
                  </div>

                  {!camera.connected ? (
                    <div className="download-card-disconnected-body">
                      <span className="disconnected-label">Connect this camera in Camera Management to download files.</span>
                    </div>
                  ) : downloadProgress[camera.serial] ? (
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
                    <div className="download-card-actions">
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => handleDownloadFromCamera(camera.serial)}
                        disabled={downloading}
                      >
                        Download {maxFiles ? `Last ${maxFiles}` : 'All'}
                      </button>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleDownloadLatest(camera.serial)}
                        disabled={downloading}
                      >
                        Download Latest
                      </button>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleBrowseCamera(camera.serial)}
                        disabled={downloading || browsing === camera.serial}
                      >
                        {browsing === camera.serial ? 'Scanning...' : 'Browse SD'}
                      </button>
                      <button
                        className="btn btn-danger btn-sm"
                        onClick={() => { setEraseConfirm(camera.serial); setEraseInput(''); }}
                        disabled={downloading || erasing === camera.serial}
                      >
                        {erasing === camera.serial ? 'Erasing...' : 'Erase SD'}
                      </button>
                    </div>
                  )}

                  {/* Erase Confirmation */}
                  {camera.connected && eraseConfirm === camera.serial && (
                    <div className="erase-confirm-box">
                      <p className="erase-confirm-text">
                        Type <strong>ERASE</strong> to confirm erasing all media from this camera:
                      </p>
                      <div className="erase-confirm-row">
                        <input
                          type="text"
                          value={eraseInput}
                          onChange={(e) => setEraseInput(e.target.value)}
                          placeholder="Type ERASE"
                          className="erase-confirm-input"
                        />
                        <button
                          className="btn btn-danger btn-sm"
                          onClick={() => handleEraseCamera(camera.serial)}
                          disabled={eraseInput !== 'ERASE'}
                        >
                          Confirm
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => { setEraseConfirm(null); setEraseInput(''); }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Browse Results */}
                  {camera.connected && browseFiles[camera.serial] && (
                    <div className="browse-results">
                      <div className="browse-summary">
                        {browseFiles[camera.serial].total_files} files ({browseFiles[camera.serial].total_size_human})
                        {' - '}
                        {browseFiles[camera.serial].video_count} videos, {browseFiles[camera.serial].other_count} other
                      </div>
                      <div className="browse-select-bar">
                        <label className="browse-select-all">
                          <input
                            type="checkbox"
                            checked={
                              (selectedFiles[camera.serial]?.size || 0) ===
                              (browseFiles[camera.serial].total_files || 0)
                            }
                            onChange={() => toggleSelectAll(camera.serial)}
                          />
                          Select All
                        </label>
                        {(selectedFiles[camera.serial]?.size || 0) > 0 && (
                          <button
                            className="btn btn-primary btn-sm"
                            onClick={() => handleDownloadSelected(camera.serial)}
                            disabled={downloading}
                          >
                            Download {selectedFiles[camera.serial].size} Selected
                          </button>
                        )}
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => setBrowseFiles(prev => { const n = {...prev}; delete n[camera.serial]; return n; })}
                        >
                          Close
                        </button>
                      </div>
                      <div className="browse-file-list">
                        {[...(browseFiles[camera.serial].videos || []), ...(browseFiles[camera.serial].others || [])].map((file, idx) => {
                          const fileKey = `${file.directory}/${file.filename}`;
                          const isSelected = selectedFiles[camera.serial]?.has(fileKey) || false;
                          return (
                            <label key={idx} className={`browse-file-item ${isSelected ? 'selected' : ''}`}>
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleFileSelection(camera.serial, file.directory, file.filename)}
                              />
                              <span className="browse-file-name">{file.filename}</span>
                              <span className="browse-file-size">{file.size_human}</span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
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
          <li><strong>Download files</strong> - Optionally specify number of files to download (e.g., 5 for last 5 files), then:
            <ul className="instructions-sublist">
              <li><strong>COHN cameras (recommended):</strong> Click "Download from All Cameras" — downloads happen in parallel via HTTPS, no WiFi switching needed</li>
              <li><strong>Non-COHN cameras:</strong> Click "Enable WiFi on All Cameras" first, wait 20 seconds, then download individually</li>
              <li>Files are downloaded in newest-first order (all files or limited to last N files)</li>
              <li>You'll see real-time progress showing which camera and which file is downloading</li>
              <li>If one camera fails, the others continue independently</li>
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
