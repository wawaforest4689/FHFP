from nicegui import ui
from datetime import datetime


def video_player(video_url: str, container_classes: str = 'w-full h-full') -> None:
    """
    创建自定义 HTML5 视频播放器，直接渲染到当前 NiceGUI 上下文。
    """
    import uuid
    unique_id = uuid.uuid4().hex[:8]  # 防止同一页面多个播放器 ID 冲突

    url = f"{video_url}?t={datetime.now().timestamp()}"

    html_content = f'''
    <div class="video-wrapper-{unique_id} {container_classes}" style="position:relative; background:#0f172a; border-radius:12px; overflow:hidden; width:100%; height:100%;">
        <video id="previewVideo_{unique_id}" style="width:100%; height:100%; object-fit:contain;" 
               src="{url}" preload="metadata" playsinline>
            您的浏览器不支持 HTML5 视频播放。
        </video>

        <!-- 居中播放按钮 -->
        <div id="bigPlayBtn_{unique_id}" onclick="togglePlay_{unique_id}()" 
             style="position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); 
                    width:64px; height:64px; background:rgba(0,0,0,0.6); border-radius:50%; 
                    display:flex; align-items:center; justify-content:center; cursor:pointer;
                    transition:opacity 0.3s; z-index:10;">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="white">
                <polygon points="8,5 8,19 19,12"></polygon>
            </svg>
        </div>

        <!-- 底部控制栏 -->
        <div id="controlsBar_{unique_id}" class="video-controls-{unique_id}"
             style="position:absolute; bottom:0; left:0; right:0; 
                    background:linear-gradient(transparent, rgba(0,0,0,0.8)); 
                    padding:20px 16px 12px; opacity:0; transition:opacity 0.3s; z-index:10;">

            <div style="position:relative; height:4px; background:rgba(255,255,255,0.3); border-radius:2px; cursor:pointer; margin-bottom:12px;"
                 id="progressContainer_{unique_id}" onclick="seek_{unique_id}(event)">
                <div id="progressBar_{unique_id}" style="height:100%; width:0%; background:#4ade80; border-radius:2px; position:relative;">
                    <div style="position:absolute; right:-6px; top:-4px; width:12px; height:12px; background:#4ade80; border-radius:50%; opacity:0; transition:opacity 0.2s;"></div>
                </div>
            </div>

            <div style="display:flex; align-items:center; justify-content:space-between;">
                <div style="display:flex; align-items:center; gap:12px;">
                    <button onclick="togglePlay_{unique_id}()" style="background:none; border:none; cursor:pointer; padding:0; color:white;">
                        <svg id="playIcon_{unique_id}" width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                            <polygon points="6,3 20,12 6,21"></polygon>
                        </svg>
                        <svg id="pauseIcon_{unique_id}" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" style="display:none;">
                            <rect x="6" y="4" width="4" height="16"></rect>
                            <rect x="14" y="4" width="4" height="16"></rect>
                        </svg>
                    </button>
                    <span id="timeDisplay_{unique_id}" style="color:#cbd5e1; font-size:13px; font-family:monospace;">00:00 / 00:00</span>
                </div>

                <div style="display:flex; align-items:center; gap:12px;">
                    <div style="display:flex; align-items:center; gap:6px;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="white">
                            <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
                        </svg>
                        <input type="range" min="0" max="1" step="0.1" value="1" 
                               oninput="setVolume_{unique_id}(this.value)" 
                               style="width:60px; height:3px; -webkit-appearance:none; background:rgba(255,255,255,0.3); border-radius:2px; outline:none;">
                    </div>
                    <button onclick="toggleFullscreen_{unique_id}()" style="background:none; border:none; cursor:pointer; padding:0; color:white;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <style>
        .video-wrapper-{unique_id}:hover .video-controls-{unique_id} {{ opacity: 1 !important; }}
        .video-wrapper-{unique_id}:hover #bigPlayBtn_{unique_id} {{ opacity: 0; pointer-events: none; }}
        #progressContainer_{unique_id}:hover #progressBar_{unique_id} > div {{ opacity: 1 !important; }}
        input[type="range"]::-webkit-slider-thumb {{
            -webkit-appearance: none; width: 10px; height: 10px; background: white; border-radius: 50%; cursor: pointer;
        }}
        video::-webkit-media-controls {{ display: none !important; }}
    </style>

    <script>
        (function() {{
            const video = document.getElementById('previewVideo_{unique_id}');
            const bigPlayBtn = document.getElementById('bigPlayBtn_{unique_id}');
            const playIcon = document.getElementById('playIcon_{unique_id}');
            const pauseIcon = document.getElementById('pauseIcon_{unique_id}');
            const progressBar = document.getElementById('progressBar_{unique_id}');
            const timeDisplay = document.getElementById('timeDisplay_{unique_id}');

            function formatTime(seconds) {{
                const m = Math.floor(seconds / 60).toString().padStart(2, '0');
                const s = Math.floor(seconds % 60).toString().padStart(2, '0');
                return m + ':' + s;
            }}

            window.togglePlay_{unique_id} = function() {{
                if (video.paused) {{
                    video.play();
                    playIcon.style.display = 'none';
                    pauseIcon.style.display = 'block';
                    bigPlayBtn.style.opacity = '0';
                    bigPlayBtn.style.pointerEvents = 'none';
                }} else {{
                    video.pause();
                    playIcon.style.display = 'block';
                    pauseIcon.style.display = 'none';
                    bigPlayBtn.style.opacity = '1';
                    bigPlayBtn.style.pointerEvents = 'auto';
                }}
            }};

            window.seek_{unique_id} = function(event) {{
                const rect = event.currentTarget.getBoundingClientRect();
                const pos = (event.clientX - rect.left) / rect.width;
                video.currentTime = pos * video.duration;
            }};

            window.setVolume_{unique_id} = function(val) {{
                video.volume = val;
            }};

            window.toggleFullscreen_{unique_id} = function() {{
                const wrapper = document.querySelector('.video-wrapper-{unique_id}');
                if (document.fullscreenElement) {{
                    document.exitFullscreen();
                }} else {{
                    wrapper.requestFullscreen();
                }}
            }};

            video.addEventListener('timeupdate', function() {{
                const progress = (video.currentTime / video.duration) * 100;
                progressBar.style.width = progress + '%';
                timeDisplay.textContent = formatTime(video.currentTime) + ' / ' + formatTime(video.duration || 0);
            }});

            video.addEventListener('ended', function() {{
                playIcon.style.display = 'block';
                pauseIcon.style.display = 'none';
                bigPlayBtn.style.opacity = '1';
                bigPlayBtn.style.pointerEvents = 'auto';
                progressBar.style.width = '0%';
            }});

            video.addEventListener('loadedmetadata', function() {{
                timeDisplay.textContent = '00:00 / ' + formatTime(video.duration);
            }});

            video.addEventListener('click', window.togglePlay_{unique_id});
        }})();
    </script>
    '''

    # 关键：使用 ui.html 直接渲染，不返回
    ui.html(html_content)

