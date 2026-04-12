/* ===================================================================
   TV Control — Display Client JS
   =================================================================== */

(function() {
    'use strict';

    // ---- Elements ----
    var screenIdle = document.getElementById('screen-idle');
    var screenIptv = document.getElementById('screen-iptv');
    var screenAds  = document.getElementById('screen-ads');

    var idleClock    = document.getElementById('idle-clock');
    var idleDate     = document.getElementById('idle-date');
    var idleStatus   = document.getElementById('idle-status');
    var connDot      = document.querySelector('.conn-dot');

    var iptvVideo    = document.getElementById('iptv-video');
    var iptvLoading  = document.getElementById('iptv-loading');
    var iptvError    = document.getElementById('iptv-error');
    var iptvErrorTxt = document.getElementById('iptv-error-text');

    var channelOverlay     = document.getElementById('channel-overlay');
    var channelOverlayName = document.getElementById('channel-overlay-name');
    var channelOverlayGroup = document.getElementById('channel-overlay-group');
    var channelOverlayLogo = document.getElementById('channel-overlay-logo');

    var adProgressBar = document.getElementById('ad-progress-bar');

    // ---- State ----
    var currentMode = 'idle';
    var hlsPlayer = null;
    var dashPlayer = null;
    var adPlaylist = [];
    var adIndex = 0;
    var adTimer = null;
    var adProgressTimer = null;
    var activeLayer = 'a';
    var channelOverlayTimer = null;
    var currentChannelName = '';
    var currentPlaylistName = '';

    // ---- Token ----
    var token = new URLSearchParams(window.location.search).get('token');

    // ---- Token Badge ----
    var tokenBadge = document.getElementById('idle-token-badge');
    var tokenValueEl = document.getElementById('idle-token-value');

    if (token) {
        // Tampilkan sebagian token: 8 karakter awal ... 8 karakter akhir
        tokenValueEl.textContent = token.length > 16
            ? token.slice(0, 8) + '...' + token.slice(-8)
            : token;
    } else {
        tokenValueEl.textContent = 'Tidak ada token';
        tokenBadge.classList.add('no-token');
    }

    // ---- Socket.IO ----
    var socket = io('/display', {
        reconnection: true,
        reconnectionAttempts: Infinity,
        reconnectionDelay: 2000,
    });

    socket.on('connect', function() {
        connDot.classList.add('connected');
        idleStatus.textContent = 'Terhubung ke server...';
        if (token) {
            socket.emit('register', { token: token });
        } else {
            idleStatus.textContent = 'Token tidak ditemukan. Buka URL dengan parameter ?token=...';
        }
    });

    socket.on('registered', function(data) {
        idleStatus.textContent = 'Terhubung — ' + (data.name || 'Perangkat');
        if (data.location) {
            idleStatus.textContent += ' (' + data.location + ')';
        }
        reportStatus();
    });

    socket.on('auth_error', function(data) {
        connDot.classList.remove('connected');
        idleStatus.textContent = data.message || 'Token tidak valid';
    });

    socket.on('disconnect', function() {
        connDot.classList.remove('connected');
        idleStatus.textContent = 'Terputus. Menghubungkan ulang...';
    });

    socket.on('command', function(data) {
        handleCommand(data);
    });

    // ---- Command Handler ----
    function handleCommand(data) {
        switch (data.action) {
            case 'play_channel':
                playChannel(data.channel);
                break;
            case 'play_ads':
                playAds(data.playlist);
                break;
            case 'idle':
                goIdle();
                break;
            case 'set_volume':
                setVolume(data.volume);
                break;
        }
    }

    // ---- Screen Switching ----
    function showScreen(screen) {
        [screenIdle, screenIptv, screenAds].forEach(function(s) {
            s.classList.remove('active');
        });
        screen.classList.add('active');
    }

    // ---- Idle Mode ----
    function goIdle() {
        currentMode = 'idle';
        stopIptv();
        stopAds();
        showScreen(screenIdle);
        idleStatus.textContent = 'Menunggu perintah...';
        currentChannelName = '';
        currentPlaylistName = '';
        reportStatus();
    }

    // ---- Clock ----
    function updateClock() {
        var now = new Date();
        var h = String(now.getHours()).padStart(2, '0');
        var m = String(now.getMinutes()).padStart(2, '0');
        idleClock.textContent = h + ':' + m;

        var days = ['Minggu', 'Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu'];
        var months = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
                      'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'];
        idleDate.textContent = days[now.getDay()] + ', ' + now.getDate() + ' ' +
                               months[now.getMonth()] + ' ' + now.getFullYear();
    }

    setInterval(updateClock, 1000);
    updateClock();

    // ---- IPTV ----
    function playChannel(channel) {
        currentMode = 'iptv';
        stopAds();
        showScreen(screenIptv);

        iptvLoading.style.display = 'flex';
        iptvError.style.display = 'none';

        var url = channel.url;
        var isLocal = channel.source_type === 'local';
        var isDash = url.includes('.mpd');
        // DASH streams are fetched directly (no proxy needed — proxy only rewrites M3U8)
        var streamUrl = isLocal || isDash ? url : '/api/proxy/stream?url=' + encodeURIComponent(url);

        stopIptv();

        if (isLocal) {
            // Video lokal: native player dengan loop
            iptvVideo.src = streamUrl;
            iptvVideo.loop = true;
            iptvVideo.addEventListener('loadeddata', function() {
                iptvVideo.play().catch(function() {});
                iptvLoading.style.display = 'none';
            }, { once: true });
            iptvVideo.addEventListener('error', function() {
                iptvLoading.style.display = 'none';
                iptvError.style.display = 'flex';
                iptvErrorTxt.textContent = 'Gagal memuat video lokal';
            }, { once: true });
        } else if (isDash) {
            // MPEG-DASH via dash.js
            iptvVideo.loop = false;
            if (typeof dashjs !== 'undefined') {
                dashPlayer = dashjs.MediaPlayer().create();
                dashPlayer.initialize(iptvVideo, streamUrl, true);
                dashPlayer.on(dashjs.MediaPlayer.events.MANIFEST_LOADED, function() {
                    iptvLoading.style.display = 'none';
                });
                dashPlayer.on(dashjs.MediaPlayer.events.ERROR, function(e) {
                    iptvLoading.style.display = 'none';
                    iptvError.style.display = 'flex';
                    iptvErrorTxt.textContent = 'Gagal memuat DASH stream';
                });
            } else {
                iptvLoading.style.display = 'none';
                iptvError.style.display = 'flex';
                iptvErrorTxt.textContent = 'Player DASH tidak tersedia';
            }
        } else if (url.includes('.m3u8') || url.includes('m3u8')) {
            iptvVideo.loop = false;
            if (Hls.isSupported()) {
                hlsPlayer = new Hls({
                    maxBufferLength: 30,
                    maxMaxBufferLength: 60,
                    startFragPrefetch: true,
                });
                hlsPlayer.loadSource(streamUrl);
                hlsPlayer.attachMedia(iptvVideo);

                hlsPlayer.on(Hls.Events.MANIFEST_PARSED, function() {
                    iptvVideo.play().catch(function() {});
                    iptvLoading.style.display = 'none';
                });

                hlsPlayer.on(Hls.Events.ERROR, function(event, data) {
                    if (data.fatal) {
                        iptvLoading.style.display = 'none';
                        iptvError.style.display = 'flex';
                        iptvErrorTxt.textContent = 'Gagal memuat stream: ' + data.type;
                        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                            setTimeout(function() {
                                hlsPlayer.startLoad();
                                iptvError.style.display = 'none';
                                iptvLoading.style.display = 'flex';
                            }, 5000);
                        }
                    }
                });
            } else if (iptvVideo.canPlayType('application/vnd.apple.mpegurl')) {
                iptvVideo.src = streamUrl;
                iptvVideo.addEventListener('loadedmetadata', function() {
                    iptvVideo.play().catch(function() {});
                    iptvLoading.style.display = 'none';
                }, { once: true });
            }
        } else {
            iptvVideo.loop = false;
            iptvVideo.src = streamUrl;
            iptvVideo.addEventListener('loadeddata', function() {
                iptvVideo.play().catch(function() {});
                iptvLoading.style.display = 'none';
            }, { once: true });
            iptvVideo.addEventListener('error', function() {
                iptvLoading.style.display = 'none';
                iptvError.style.display = 'flex';
                iptvErrorTxt.textContent = 'Gagal memuat stream';
            }, { once: true });
        }

        // Channel overlay
        currentChannelName = channel.name;
        channelOverlayName.textContent = channel.name;
        channelOverlayGroup.textContent = channel.group || '';
        channelOverlayLogo.innerHTML = channel.logo_url
            ? '<img src="' + channel.logo_url + '" alt="">'
            : '<i class="fa-solid fa-tv" style="color:rgba(255,255,255,0.4);font-size:18px"></i>';

        showChannelOverlay();
        reportStatus();
    }

    function stopIptv() {
        if (hlsPlayer) {
            hlsPlayer.destroy();
            hlsPlayer = null;
        }
        if (dashPlayer) {
            dashPlayer.destroy();
            dashPlayer = null;
        }
        iptvVideo.pause();
        iptvVideo.removeAttribute('src');
        iptvVideo.load();
        iptvLoading.style.display = 'none';
        iptvError.style.display = 'none';
    }

    function showChannelOverlay() {
        channelOverlay.classList.add('visible');
        clearTimeout(channelOverlayTimer);
        channelOverlayTimer = setTimeout(function() {
            channelOverlay.classList.remove('visible');
        }, 5000);
    }

    // ---- Ads ----
    function playAds(playlist) {
        currentMode = 'ads';
        stopIptv();
        showScreen(screenAds);

        adPlaylist = playlist.ads || [];
        currentPlaylistName = playlist.name;
        adIndex = 0;

        if (adPlaylist.length === 0) {
            goIdle();
            return;
        }

        showAd();
        reportStatus();
    }

    function showAd() {
        if (adPlaylist.length === 0) return;

        var ad = adPlaylist[adIndex];
        var nextLayer = activeLayer === 'a' ? 'b' : 'a';

        var layerEl = document.getElementById('ad-layer-' + nextLayer);
        var imgEl   = document.getElementById('ad-img-' + nextLayer);
        var vidEl   = document.getElementById('ad-vid-' + nextLayer);
        var prevLayer = document.getElementById('ad-layer-' + activeLayer);

        // Reset next layer
        imgEl.style.display = 'none';
        vidEl.style.display = 'none';
        vidEl.pause();
        vidEl.removeAttribute('src');

        if (ad.file_type === 'image' || ad.type === 'image') {
            imgEl.src = ad.url;
            imgEl.style.display = 'block';
            layerEl.style.opacity = '0';

            imgEl.onload = function() {
                // Crossfade
                layerEl.style.opacity = '1';
                prevLayer.style.opacity = '0';
                activeLayer = nextLayer;

                startAdProgress(ad.duration || 10);

                clearTimeout(adTimer);
                adTimer = setTimeout(function() {
                    advanceAd();
                }, (ad.duration || 10) * 1000);
            };
        } else {
            vidEl.src = ad.url;
            vidEl.style.display = 'block';
            vidEl.load();
            layerEl.style.opacity = '0';

            vidEl.oncanplay = function() {
                layerEl.style.opacity = '1';
                prevLayer.style.opacity = '0';
                activeLayer = nextLayer;
                vidEl.play().catch(function() {});
            };

            vidEl.onloadedmetadata = function() {
                startAdProgress(vidEl.duration);
            };

            vidEl.onended = function() {
                advanceAd();
            };

            vidEl.onerror = function() {
                advanceAd();
            };
        }
    }

    function advanceAd() {
        clearTimeout(adTimer);
        clearInterval(adProgressTimer);
        adIndex = (adIndex + 1) % adPlaylist.length;
        showAd();
    }

    function startAdProgress(duration) {
        var start = Date.now();
        var total = duration * 1000;
        clearInterval(adProgressTimer);
        adProgressBar.style.width = '0%';

        adProgressTimer = setInterval(function() {
            var elapsed = Date.now() - start;
            var pct = Math.min((elapsed / total) * 100, 100);
            adProgressBar.style.width = pct + '%';
            if (pct >= 100) clearInterval(adProgressTimer);
        }, 50);
    }

    function stopAds() {
        clearTimeout(adTimer);
        clearInterval(adProgressTimer);
        adPlaylist = [];
        adIndex = 0;

        ['a', 'b'].forEach(function(layer) {
            var img = document.getElementById('ad-img-' + layer);
            var vid = document.getElementById('ad-vid-' + layer);
            img.style.display = 'none';
            img.removeAttribute('src');
            vid.style.display = 'none';
            vid.pause();
            vid.removeAttribute('src');
            document.getElementById('ad-layer-' + layer).style.opacity = '';
        });

        adProgressBar.style.width = '0%';
    }

    // ---- Volume ----
    function setVolume(vol) {
        var v = Math.max(0, Math.min(100, vol)) / 100;
        iptvVideo.volume = v;
        document.getElementById('ad-vid-a').volume = v;
        document.getElementById('ad-vid-b').volume = v;
    }

    // ---- Fullscreen ----
    window.toggleFullscreen = function() {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(function() {});
        } else {
            document.exitFullscreen().catch(function() {});
        }
    };

    // ---- Status Reporting ----
    function reportStatus() {
        var playing = '-';
        if (currentMode === 'iptv') playing = currentChannelName;
        if (currentMode === 'ads') playing = currentPlaylistName;

        socket.emit('status', {
            mode: currentMode,
            playing: playing,
        });
    }

    setInterval(reportStatus, 10000);

    // ---- Init ----
    goIdle();

})();
