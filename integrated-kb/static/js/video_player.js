/**
 * HLS playback for videos stored in knowledge-base pages.
 *
 * Existing HTML keeps its original /api/videos/<uuid> URL.
 * HLS is used only when its playlist is available.
 */

const VIDEO_URL_RE =
  /^\/api\/videos\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i;

const HLS_LIBRARY_URL = '/static/js/vendor/hls.min.js';

const activePlayers = new Map();

let libraryPromise = null;

function getVideoId(video) {
  const raw = video.getAttribute('src');

  if (!raw) return null;

  const match = VIDEO_URL_RE.exec(raw);

  return match ? match[1] : null;
}

function loadHlsLibrary() {
  if (window.Hls) {
    return Promise.resolve(window.Hls);
  }

  if (libraryPromise) {
    return libraryPromise;
  }

  libraryPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');

    script.src = HLS_LIBRARY_URL;
    script.async = true;

    script.onload = () => {
      if (window.Hls) {
        resolve(window.Hls);
      } else {
        reject(new Error('hls.js did not initialize'));
      }
    };

    script.onerror = () => {
      reject(new Error('Failed to load hls.js'));
    };

    document.head.appendChild(script);
  }).catch(error => {
    libraryPromise = null;
    throw error;
  });

  return libraryPromise;
}

function restoreOriginal(video, originalSrc) {
  if (!video.isConnected) return;

  // hls.js can attach a MediaSource without changing the src attribute.
  // Explicitly restore the original URL after a fatal HLS error.
  video.src = originalSrc;
  video.load();
}

async function initializeVideo(video) {
  if (activePlayers.has(video)) return;

  const videoId = getVideoId(video);

  if (!videoId) return;

  const originalSrc = video.getAttribute('src');

  const playlistUrl =
    `/api/videos/${videoId}/hls/index.m3u8`;

  const playerState = {
    cancelled: false,
    hls: null,
    originalSrc
  };

  activePlayers.set(video, playerState);

  try {
    const response = await fetch(playlistUrl, {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store'
    });

    if (playerState.cancelled || !video.isConnected) return;

    if (!response.ok) {
      return;
    }

    const contentType =
      response.headers.get('content-type') || '';

    if (!contentType.includes('mpegurl')) {
      return;
    }

    // Safari and other browsers with native HLS support.
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = playlistUrl;
      video.load();
      return;
    }

    const Hls = await loadHlsLibrary();

    if (playerState.cancelled || !video.isConnected) return;

    if (!Hls.isSupported()) {
      return;
    }

    const hls = new Hls({
      enableWorker: true
    });

    playerState.hls = hls;

    hls.on(Hls.Events.ERROR, (_event, data) => {
      if (!data.fatal || playerState.cancelled) return;

      console.warn('HLS playback failed:', data.type);

      hls.destroy();
      playerState.hls = null;

      restoreOriginal(video, originalSrc);
    });

    hls.loadSource(playlistUrl);
    hls.attachMedia(video);

  } catch (error) {
    console.warn('HLS unavailable, using original video:', error);
    restoreOriginal(video, originalSrc);
  }
}

/**
 * Initialize videos after their article is attached to the DOM.
 */
export function initializeVideoPlayers(container) {
  if (!container) return;

  const videos = container.querySelectorAll('video');

  for (const video of videos) {
    initializeVideo(video);
  }
}

/**
 * Release hls.js instances before replacing the current view.
 */
export function destroyVideoPlayers() {
  for (const [video, playerState] of activePlayers) {
    playerState.cancelled = true;

    if (playerState.hls) {
      playerState.hls.destroy();
      playerState.hls = null;
    }

    activePlayers.delete(video);
  }
}
