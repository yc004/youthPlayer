(function () {
    var ganttActiveDay = 0;

    function setText(selector, value) {
        var node = document.querySelector(selector);
        if (node && typeof value !== "undefined" && value !== null && value !== "") {
            node.textContent = value;
        }
    }

    function refreshStatus() {
        var request = new XMLHttpRequest();
        request.open("GET", "/api/status", true);
        request.onreadystatechange = function () {
            if (request.readyState !== 4 || request.status !== 200) {
                return;
            }

            try {
                var payload = JSON.parse(request.responseText);
                var player = payload.player || {};
                var active = payload.active_schedule;
                var monitor = payload.monitor || {};

                setText("[data-role='player-state']", player.is_playing ? "Playing" : "Idle");
                setText("[data-role='screen-name']", player.screen_name || "Unknown screen");
                setText("[data-role='current-source']", player.current_source || "No source");
                setText("[data-role='state-detail']", player.state || "Idle");
                setText("[data-role='backend']", player.backend || "idle");
                setText("[data-role='last-error']", player.last_error || "None");
                setText("[data-role='server-time']", payload.server_time || "--");
                setText("[data-role='active-schedule']", active ? active.name : "None");
                setText("[data-role='monitor-time']", monitor.captured_at || "Waiting capture");

                var monitorImg = document.querySelector("[data-role='monitor-preview']");
                if (monitorImg && monitor.frame_url) {
                    monitorImg.src = monitor.frame_url + "?t=" + Date.now();
                }

                var badge = document.querySelector("[data-role='play-badge']");
                if (badge) {
                    badge.textContent = player.is_playing ? "Playing" : "Standby";
                    badge.className = "chip " + (player.is_playing ? "success" : "muted");
                }

                renderMonitorPlayback(payload);
            } catch (_err) {
                // ignore
            }
        };
        request.send();
    }

    function renderMonitorPlayback(payload) {
        var scheduleNode = document.querySelector("[data-role='monitor-active-schedule']");
        if (!scheduleNode) return;

        var active = payload && payload.active_schedule ? payload.active_schedule : null;
        var player = payload && payload.player ? payload.player : {};
        var listNode = document.querySelector("[data-role='monitor-play-count-list']");
        var positionNode = document.querySelector("[data-role='monitor-video-position']");
        var countNode = document.querySelector("[data-role='monitor-video-play-count']");
        var progressNode = document.querySelector("[data-role='monitor-video-progress']");

        var scheduleName = active && active.name ? active.name : "无活动任务";
        scheduleNode.textContent = scheduleName;

        var items = active && Array.isArray(active.playlist_items) ? active.playlist_items : [];
        var size = Number(player.playlist_size || 0);
        if (items.length > size) size = items.length;

        var index = Number(player.playlist_index || 0);
        if (!isFinite(index) || index < 0) index = 0;
        var positionText = size > 0 ? (index + 1) + " / " + size : "-";
        if (positionNode) positionNode.textContent = positionText;

        var counts = Array.isArray(player.playlist_play_counts) ? player.playlist_play_counts : [];
        var currentCount = (index >= 0 && index < counts.length) ? Number(counts[index] || 0) : 0;
        if (countNode) countNode.textContent = size > 0 ? String(currentCount) : "-";
        if (progressNode) {
            progressNode.textContent = formatProgressText(player && player.playback_progress);
        }

        if (!listNode) return;
        listNode.innerHTML = "";
        if (!size) {
            var empty = document.createElement("div");
            empty.className = "playlist-order-empty";
            empty.textContent = "当前没有播放列表。";
            listNode.appendChild(empty);
            return;
        }

        for (var i = 0; i < size; i += 1) {
            var row = document.createElement("div");
            row.className = "playlist-order-item";
            if (i === index && size > 0) row.className += " is-current";

            var seq = document.createElement("span");
            seq.className = "playlist-order-handle";
            seq.textContent = "#" + String(i + 1);

            var path = document.createElement("span");
            path.className = "playlist-order-path";
            var raw = items[i] || "";
            path.textContent = displayVideoName(raw) || ("视频 " + String(i + 1));
            path.title = raw || "";

            var stat = document.createElement("span");
            stat.className = "chip info";
            stat.textContent = "已播 " + String(Number(counts[i] || 0)) + " 次";

            row.appendChild(seq);
            row.appendChild(path);
            row.appendChild(stat);
            listNode.appendChild(row);
        }
    }

    function formatProgressText(progress) {
        var p = progress || {};
        var current = Number(p.current_seconds || 0);
        var duration = Number(p.duration_seconds || 0);
        var percent = Number(p.progress_percent || 0);
        if (!isFinite(current) || current < 0) current = 0;
        if (!isFinite(duration) || duration < 0) duration = 0;
        if (!isFinite(percent) || percent < 0) percent = 0;
        if (percent > 100) percent = 100;
        if (duration <= 0) return "未知";
        return secondsToClock(current) + " / " + secondsToClock(duration) + " (" + percent.toFixed(1) + "%)";
    }

    function secondsToClock(seconds) {
        var s = Math.max(0, Math.floor(Number(seconds) || 0));
        var h = Math.floor(s / 3600);
        var m = Math.floor((s % 3600) / 60);
        var sec = s % 60;
        if (h > 0) return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
        return String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
    }

    function displayVideoName(raw) {
        var text = String(raw || "").trim();
        if (!text) return "";
        var cleaned = text.replace(/[?#].*$/, "").replace(/\/+$/, "");
        var parts = cleaned.split(/[\\/]/);
        var name = parts.length ? parts[parts.length - 1] : cleaned;
        if (!name) return text;
        try {
            return decodeURIComponent(name);
        } catch (_err) {
            return name;
        }
    }

    function minutesToText(minutes) {
        var h = Math.floor(minutes / 60);
        var m = minutes % 60;
        return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
    }

    function createHourScale() {
        var scale = document.createElement("div");
        scale.className = "gantt-hours";
        for (var h = 0; h <= 24; h += 2) {
            var tick = document.createElement("span");
            tick.textContent = String(h).padStart(2, "0") + ":00";
            scale.appendChild(tick);
        }
        return scale;
    }

    function createRow(schedule) {
        var row = document.createElement("div");
        row.className = "gantt-row" + (schedule.is_active ? "" : " muted");

        var label = document.createElement("div");
        label.className = "gantt-row-label";
        label.textContent = schedule.name + " (屏幕 " + schedule.screen_index + ")";

        var lane = document.createElement("div");
        lane.className = "gantt-lane";

        var bar = document.createElement("div");
        bar.className = "gantt-bar";
        var left = Math.max(0, Math.min(100, (schedule.start_minutes / 1440) * 100));
        var width = Math.max(1, ((schedule.end_minutes - schedule.start_minutes) / 1440) * 100);
        bar.style.left = left + "%";
        bar.style.width = width + "%";
        bar.title = schedule.name + " " + minutesToText(schedule.start_minutes) + "-" + minutesToText(schedule.end_minutes);
        bar.textContent = minutesToText(schedule.start_minutes) + "-" + minutesToText(schedule.end_minutes);
        lane.appendChild(bar);

        var nowMarker = document.createElement("div");
        nowMarker.className = "gantt-now-marker";
        lane.appendChild(nowMarker);

        row.appendChild(label);
        row.appendChild(lane);
        return row;
    }

    function filterByDay(schedules, day) {
        return schedules.filter(function (item) {
            if (item.is_weekly) {
                return (item.weekly_days || []).indexOf(day) >= 0;
            }
            return item.start_weekday === day;
        });
    }

    function renderGantt(day) {
        var board = document.querySelector("[data-role='gantt-board']");
        var dataNode = document.getElementById("timeline-data");
        if (!board || !dataNode) return;

        var schedules = [];
        try {
            schedules = JSON.parse(dataNode.textContent || "[]");
        } catch (_err) {
            schedules = [];
        }

        var rows = filterByDay(schedules, day).sort(function (a, b) {
            return a.start_minutes - b.start_minutes;
        });

        board.innerHTML = "";
        board.appendChild(createHourScale());

        if (!rows.length) {
            var empty = document.createElement("div");
            empty.className = "empty-state";
            empty.textContent = "当天没有计划任务。";
            board.appendChild(empty);
            return;
        }

        rows.forEach(function (item) {
            board.appendChild(createRow(item));
        });
        refreshGanttNowMarker(day);
    }

    function refreshGanttNowMarker(day) {
        var now = new Date();
        var isTodayTab = now.getDay() === ((day + 1) % 7); // JS: Sun=0, app: Mon=0
        var nowMinutes = now.getHours() * 60 + now.getMinutes();
        var left = (nowMinutes / 1440) * 100;

        document.querySelectorAll(".gantt-row").forEach(function (row) {
            var bar = row.querySelector(".gantt-bar");
            var marker = row.querySelector(".gantt-now-marker");
            if (!marker) return;
            if (!isTodayTab) {
                marker.style.display = "none";
                row.classList.remove("current-live");
                return;
            }
            marker.style.display = "block";
            marker.style.left = left + "%";

            var isCurrent = false;
            if (bar) {
                var txt = bar.textContent || "";
                var seg = txt.split("-");
                if (seg.length === 2) {
                    var s = seg[0].split(":");
                    var e = seg[1].split(":");
                    if (s.length === 2 && e.length === 2) {
                        var sm = Number(s[0]) * 60 + Number(s[1]);
                        var em = Number(e[0]) * 60 + Number(e[1]);
                        isCurrent = nowMinutes >= sm && nowMinutes < em;
                    }
                }
            }
            row.classList.toggle("current-live", isCurrent);
        });
    }

    function initGantt() {
        var tabs = document.querySelectorAll("[data-role='gantt-tabs'] .gantt-tab");
        if (!tabs.length) return;

        ganttActiveDay = 0;
        renderGantt(ganttActiveDay);

        tabs.forEach(function (tab) {
            tab.addEventListener("click", function () {
                tabs.forEach(function (x) {
                    x.classList.remove("active");
                });
                tab.classList.add("active");
                ganttActiveDay = Number(tab.getAttribute("data-day") || "0");
                renderGantt(ganttActiveDay);
            });
        });

        window.setInterval(function () {
            refreshGanttNowMarker(ganttActiveDay);
        }, 1000);
    }

    function initFileBrowser() {
        var backdrop = document.querySelector("[data-role='file-browser-backdrop']");
        if (!backdrop) return;

        var listNode = backdrop.querySelector("[data-role='fb-list']");
        var cwdNode = backdrop.querySelector("[data-role='fb-cwd']");
        var pathInput = backdrop.querySelector("[data-role='fb-path-input']");
        var importBtn = backdrop.querySelector("[data-role='fb-import-files']");
        var nextcloudBtn = backdrop.querySelector("[data-role='fb-nextcloud']");
        var currentPath = "";
        var currentParent = "";
        var currentEntries = [];
        var currentSource = "local";
        var activeInput = null;
        var activeMode = "single";

        function parseLines(text) {
            return String(text || "")
                .replace(/\r\n/g, "\n")
                .split("\n")
                .map(function (line) { return line.trim(); })
                .filter(Boolean);
        }

        function normalizeLines(lines) {
            var out = [];
            var seen = {};
            lines.forEach(function (line) {
                var key = String(line || "").trim();
                if (!key || seen[key]) return;
                seen[key] = true;
                out.push(key);
            });
            return out;
        }

        function setPlaylistValue(input, lines) {
            if (!input) return;
            input.value = normalizeLines(lines).join("\n");
            input.dispatchEvent(new Event("input", { bubbles: true }));
        }

        function appendPlaylistItems(input, paths) {
            if (!input || !paths || !paths.length) return;
            var merged = parseLines(input.value).concat(paths);
            setPlaylistValue(input, merged);
        }

        function isLikelyVideoPath(path) {
            return /\.(mp4|mkv|avi|mov|wmv|flv|m3u8|ts|webm|mpg|mpeg)$/i.test(String(path || ""));
        }

        function closeBrowser() {
            backdrop.hidden = true;
            activeInput = null;
            activeMode = "single";
            currentSource = "local";
            if (importBtn) importBtn.hidden = true;
        }

        function openBrowser(input, mode) {
            activeInput = input;
            activeMode = mode || "single";
            backdrop.hidden = false;
            if (importBtn) importBtn.hidden = activeMode !== "playlist";
            loadPath("", "local");
        }

        function renderEntries(entries) {
            listNode.innerHTML = "";
            if (!entries.length) {
                var empty = document.createElement("div");
                empty.className = "empty-state";
                empty.textContent = "Empty directory";
                listNode.appendChild(empty);
                return;
            }
            entries.forEach(function (item) {
                var row = document.createElement("div");
                row.className = "fb-row";

                var label = document.createElement("span");
                label.className = "fb-name" + (item.is_dir ? " dir" : " file");
                label.textContent = (item.is_dir ? "[DIR] " : "[FILE] ") + item.name;
                label.title = item.path;

                var action = document.createElement("button");
                action.type = "button";
                action.className = "btn btn-secondary";
                action.textContent = item.is_dir ? "Open" : (activeMode === "playlist" ? "Add" : "Select");
                action.addEventListener("click", function () {
                    if (item.is_dir) {
                        loadPath(item.path);
                    } else if (activeInput) {
                        if (activeMode === "playlist") {
                            appendPlaylistItems(activeInput, [item.path]);
                        } else {
                            activeInput.value = item.path;
                            closeBrowser();
                        }
                    }
                });

                row.appendChild(label);
                row.appendChild(action);
                listNode.appendChild(row);
            });
        }

        function loadPath(path, source) {
            var targetSource = source || currentSource || "local";
            currentSource = targetSource;
            var url = "/api/browse";
            var query = [];
            if (targetSource && targetSource !== "local") {
                query.push("source=" + encodeURIComponent(targetSource));
            }
            if (path) query.push("path=" + encodeURIComponent(path));
            if (query.length) url += "?" + query.join("&");
            var request = new XMLHttpRequest();
            request.open("GET", url, true);
            request.onreadystatechange = function () {
                if (request.readyState !== 4) return;
                if (request.status !== 200) {
                    listNode.innerHTML = "<div class='empty-state'>Path access failed</div>";
                    return;
                }
                try {
                    var payload = JSON.parse(request.responseText);
                    currentPath = payload.cwd || "";
                    currentParent = payload.parent || "";
                    currentEntries = payload.entries || [];
                    currentSource = payload.source || targetSource;
                    pathInput.value = currentPath;
                    cwdNode.textContent = "Current path (" + currentSource + "): " + (currentPath || "root");
                    renderEntries(currentEntries);
                } catch (_err) {
                    listNode.innerHTML = "<div class='empty-state'>Data parse failed</div>";
                }
            };
            request.send();
        }

        document.querySelectorAll(".btn-path-browse").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var picker = btn.closest(".path-picker");
                var input = picker && picker.querySelector(".content-path-input");
                if (input) openBrowser(input, "single");
            });
        });
        document.querySelectorAll(".btn-playlist-import").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var field = btn.closest(".field");
                var input = field && field.querySelector("textarea[name='playlist_paths']");
                if (input) openBrowser(input, "playlist");
            });
        });

        backdrop.querySelector("[data-role='fb-close']").addEventListener("click", closeBrowser);
        backdrop.querySelector("[data-role='fb-roots']").addEventListener("click", function () {
            loadPath("", "local");
        });
        if (nextcloudBtn) {
            nextcloudBtn.addEventListener("click", function () {
                loadPath("/", "nextcloud");
            });
        }
        backdrop.querySelector("[data-role='fb-up']").addEventListener("click", function () {
            if (currentParent) {
                loadPath(currentParent, currentSource);
            } else {
                loadPath("", currentSource);
            }
        });
        backdrop.querySelector("[data-role='fb-go']").addEventListener("click", function () {
            loadPath(pathInput.value.trim(), currentSource);
        });
        backdrop.querySelector("[data-role='fb-use-dir']").addEventListener("click", function () {
            if (activeInput) {
                if (activeMode === "playlist") {
                    var allVideos = currentEntries
                        .filter(function (item) { return !item.is_dir && isLikelyVideoPath(item.path); })
                        .map(function (item) { return item.path; });
                    appendPlaylistItems(activeInput, allVideos);
                } else {
                    activeInput.value = currentPath;
                    closeBrowser();
                }
            }
        });
        if (importBtn) {
            importBtn.addEventListener("click", function () {
                if (!activeInput || activeMode !== "playlist") return;
                var allVideos = currentEntries
                    .filter(function (item) { return !item.is_dir && isLikelyVideoPath(item.path); })
                    .map(function (item) { return item.path; });
                appendPlaylistItems(activeInput, allVideos);
            });
        }
        pathInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                loadPath(pathInput.value.trim());
            }
        });
        backdrop.addEventListener("click", function (e) {
            if (e.target === backdrop) closeBrowser();
        });
    }

    function initPlaylistEditor() {
        function parseLines(text) {
            return String(text || "")
                .replace(/\r\n/g, "\n")
                .split("\n")
                .map(function (line) { return line.trim(); })
                .filter(Boolean);
        }

        function dedupe(lines) {
            var out = [];
            var seen = {};
            lines.forEach(function (line) {
                if (!line || seen[line]) return;
                seen[line] = true;
                out.push(line);
            });
            return out;
        }

        document.querySelectorAll(".schedule-form").forEach(function (form) {
            var textarea = form.querySelector("textarea[name='playlist_paths']");
            if (!textarea) return;
            var primaryInput = form.querySelector("input[name='content_path']");
            var field = textarea.closest(".field");
            var list = field && field.querySelector("[data-role='playlist-order-list']");
            if (!list) return;

            var dragIndex = -1;

            function getItems() {
                return dedupe(parseLines(textarea.value));
            }

            function setItems(items) {
                var normalized = dedupe(items);
                textarea.value = normalized.join("\n");
                if (primaryInput) primaryInput.value = normalized[0] || "";
                textarea.dispatchEvent(new Event("input", { bubbles: true }));
            }

            function moveItem(items, from, to) {
                if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items;
                var next = items.slice();
                var moved = next.splice(from, 1)[0];
                next.splice(to, 0, moved);
                return next;
            }

            function render() {
                var items = getItems();
                if (primaryInput) primaryInput.value = items[0] || "";
                list.innerHTML = "";
                if (!items.length) {
                    var empty = document.createElement("div");
                    empty.className = "playlist-order-empty";
                    empty.textContent = "No playlist items yet.";
                    list.appendChild(empty);
                    return;
                }
                items.forEach(function (path, index) {
                    var row = document.createElement("div");
                    row.className = "playlist-order-item";
                    row.draggable = true;
                    row.dataset.index = String(index);

                    var handle = document.createElement("span");
                    handle.className = "playlist-order-handle";
                    handle.textContent = "drag";

                    var text = document.createElement("span");
                    text.className = "playlist-order-path";
                    text.textContent = path;
                    text.title = path;

                    var actions = document.createElement("div");
                    actions.className = "playlist-order-actions";

                    var upBtn = document.createElement("button");
                    upBtn.type = "button";
                    upBtn.className = "btn btn-secondary";
                    upBtn.dataset.action = "up";
                    upBtn.dataset.index = String(index);
                    upBtn.textContent = "Up";

                    var downBtn = document.createElement("button");
                    downBtn.type = "button";
                    downBtn.className = "btn btn-secondary";
                    downBtn.dataset.action = "down";
                    downBtn.dataset.index = String(index);
                    downBtn.textContent = "Down";

                    var removeBtn = document.createElement("button");
                    removeBtn.type = "button";
                    removeBtn.className = "btn btn-danger";
                    removeBtn.dataset.action = "remove";
                    removeBtn.dataset.index = String(index);
                    removeBtn.textContent = "Remove";

                    actions.appendChild(upBtn);
                    actions.appendChild(downBtn);
                    actions.appendChild(removeBtn);
                    row.appendChild(handle);
                    row.appendChild(text);
                    row.appendChild(actions);

                    row.addEventListener("dragstart", function () {
                        dragIndex = index;
                        row.classList.add("dragging");
                    });
                    row.addEventListener("dragend", function () {
                        dragIndex = -1;
                        row.classList.remove("dragging");
                    });
                    row.addEventListener("dragover", function (ev) {
                        ev.preventDefault();
                    });
                    row.addEventListener("drop", function (ev) {
                        ev.preventDefault();
                        if (dragIndex < 0) return;
                        var dropIndex = Number(row.dataset.index);
                        setItems(moveItem(getItems(), dragIndex, dropIndex));
                    });

                    list.appendChild(row);
                });
            }

            list.addEventListener("click", function (ev) {
                var btn = ev.target.closest("button[data-action]");
                if (!btn) return;
                var action = btn.dataset.action;
                var index = Number(btn.dataset.index);
                var items = getItems();
                if (action === "remove") {
                    items.splice(index, 1);
                    setItems(items);
                    return;
                }
                if (action === "up") {
                    setItems(moveItem(items, index, Math.max(0, index - 1)));
                    return;
                }
                if (action === "down") {
                    setItems(moveItem(items, index, Math.min(items.length - 1, index + 1)));
                }
            });

            textarea.addEventListener("input", render);
            render();
        });
    }

    function refreshMonitorOnly() {
        var monitorImg = document.querySelector("[data-role='monitor-preview']");
        if (!monitorImg) return;
        var request = new XMLHttpRequest();
        request.open("GET", "/api/monitor", true);
        request.onreadystatechange = function () {
            if (request.readyState !== 4 || request.status !== 200) return;
            try {
                var payload = JSON.parse(request.responseText);
                setText("[data-role='monitor-time']", payload.captured_at || "Waiting capture");
                if (payload.frame_url) {
                    monitorImg.src = payload.frame_url + "?t=" + Date.now();
                }
            } catch (_err) {
                // ignore
            }
        };
        request.send();
    }

    function initMonitorPolling() {
        if (!document.querySelector("[data-role='monitor-active-schedule']")) return;
        refreshStatus();
        window.setInterval(refreshStatus, 5000);
    }

    function initNextcloudSettingsTools() {
        var testBtn = document.querySelector("[data-role='nc-test-btn']");
        var previewBtn = document.querySelector("[data-role='nc-preview-btn']");
        var openBtn = document.querySelector("[data-role='nc-preview-open']");
        var resultNode = document.querySelector("[data-role='nc-test-result']");
        var pathNode = document.querySelector("[data-role='nc-preview-path']");
        var listNode = document.querySelector("[data-role='nc-preview-list']");
        var urlInput = document.querySelector("input[name='nextcloud_url']");
        var userInput = document.querySelector("input[name='nextcloud_username']");
        var passInput = document.querySelector("input[name='nextcloud_password']");
        var rootInput = document.querySelector("input[name='nextcloud_root']");
        var skipSslInput = document.querySelector("input[name='nextcloud_skip_ssl_verify']");
        if (!testBtn || !previewBtn || !openBtn || !resultNode || !pathNode || !listNode) return;

        function currentQuery(extra) {
            var q = [];
            var url = (urlInput && urlInput.value || "").trim();
            var username = (userInput && userInput.value || "").trim();
            var password = (passInput && passInput.value || "").trim();
            var root = (rootInput && rootInput.value || "").trim();
            var skipSsl = !!(skipSslInput && skipSslInput.checked);
            if (url) q.push("url=" + encodeURIComponent(url));
            if (username) q.push("username=" + encodeURIComponent(username));
            if (password) q.push("password=" + encodeURIComponent(password));
            if (root) q.push("root=" + encodeURIComponent(root));
            q.push("skip_ssl_verify=" + (skipSsl ? "1" : "0"));
            if (extra) q.push(extra);
            return q.join("&");
        }

        function renderPreview(payload) {
            listNode.innerHTML = "";
            var items = (payload && payload.entries) || [];
            if (!items.length) {
                var empty = document.createElement("div");
                empty.className = "playlist-order-empty";
                empty.textContent = "No files or folders.";
                listNode.appendChild(empty);
                return;
            }

            items.forEach(function (item) {
                var row = document.createElement("div");
                row.className = "playlist-order-item";

                var icon = document.createElement("span");
                icon.className = "playlist-order-handle";
                icon.textContent = item.is_dir ? "DIR" : "FILE";

                var text = document.createElement("span");
                text.className = "playlist-order-path";
                text.textContent = item.name + (item.is_dir ? "/" : "");
                text.title = item.path || "";

                var actions = document.createElement("div");
                actions.className = "playlist-order-actions";
                if (item.is_dir) {
                    var open = document.createElement("button");
                    open.type = "button";
                    open.className = "btn btn-secondary";
                    open.textContent = "Open";
                    open.addEventListener("click", function () {
                        pathNode.value = item.path || "/";
                        loadPreview(item.path || "/");
                    });
                    actions.appendChild(open);
                }

                row.appendChild(icon);
                row.appendChild(text);
                row.appendChild(actions);
                listNode.appendChild(row);
            });
        }

        function loadPreview(path) {
            var safePath = (path || "/").trim() || "/";
            var query = currentQuery("path=" + encodeURIComponent(safePath));
            var req = new XMLHttpRequest();
            req.open("GET", "/api/nextcloud/preview?" + query, true);
            req.onreadystatechange = function () {
                if (req.readyState !== 4) return;
                if (req.status !== 200) {
                    try {
                        var fail = JSON.parse(req.responseText || "{}");
                        resultNode.textContent = fail.error || "Preview failed.";
                    } catch (_err) {
                        resultNode.textContent = "Preview failed.";
                    }
                    return;
                }
                try {
                    var payload = JSON.parse(req.responseText || "{}");
                    pathNode.value = payload.cwd || safePath;
                    resultNode.textContent = "Preview loaded: " + (payload.cwd || safePath);
                    renderPreview(payload);
                } catch (_err2) {
                    resultNode.textContent = "Preview parse failed.";
                }
            };
            req.send();
        }

        testBtn.addEventListener("click", function () {
            var req = new XMLHttpRequest();
            var query = currentQuery();
            req.open("GET", "/api/nextcloud/test" + (query ? "?" + query : ""), true);
            req.onreadystatechange = function () {
                if (req.readyState !== 4) return;
                if (req.status !== 200) {
                    try {
                        var fail = JSON.parse(req.responseText || "{}");
                        resultNode.textContent = fail.error || "Connection test failed.";
                    } catch (_err) {
                        resultNode.textContent = "Connection test failed.";
                    }
                    return;
                }
                try {
                    var payload = JSON.parse(req.responseText || "{}");
                    resultNode.textContent = payload.message + " folders=" + payload.folder_count + ", entries=" + payload.entry_count;
                } catch (_err2) {
                    resultNode.textContent = "Connection test parse failed.";
                }
            };
            req.send();
        });

        previewBtn.addEventListener("click", function () {
            loadPreview(pathNode.value || "/");
        });
        openBtn.addEventListener("click", function () {
            loadPreview(pathNode.value || "/");
        });
    }

    function extractTimePart(value) {
        if (!value) return "";
        if (value.indexOf("T") >= 0) {
            return value.split("T")[1].slice(0, 5);
        }
        return value.slice(0, 5);
    }

    function todayDatePart() {
        var now = new Date();
        var y = now.getFullYear();
        var m = String(now.getMonth() + 1).padStart(2, "0");
        var d = String(now.getDate()).padStart(2, "0");
        return y + "-" + m + "-" + d;
    }

    function switchToTimeInput(input) {
        if (!input) return;
        if (input.value && input.value.indexOf("T") >= 0) {
            input.dataset.datetimeValue = input.value;
        }
        var from = input.value || input.dataset.datetimeValue || "";
        var timePart = extractTimePart(from);
        input.type = "time";
        input.value = timePart;
    }

    function switchToDatetimeInput(input) {
        if (!input) return;
        var previous = input.dataset.datetimeValue || "";
        if (!input.value && !previous) {
            input.type = "datetime-local";
            input.value = "";
            return;
        }
        var timePart = extractTimePart(input.value || previous) || "00:00";
        var datePart = previous && previous.indexOf("T") >= 0 ? previous.split("T")[0] : todayDatePart();
        var next = datePart + "T" + timePart;
        input.type = "datetime-local";
        input.value = next;
        input.dataset.datetimeValue = next;
    }

    function initScheduleWeeklyInputMode() {
        document.querySelectorAll(".schedule-form").forEach(function (form) {
            var weeklyToggle = form.querySelector("input[name='is_weekly']");
            var startInput = form.querySelector("input[name='start_time']");
            var endInput = form.querySelector("input[name='end_time']");
            if (!weeklyToggle || !startInput || !endInput) return;

            [startInput, endInput].forEach(function (input) {
                if (input.type === "datetime-local" && input.value) {
                    input.dataset.datetimeValue = input.value;
                }
                input.addEventListener("change", function () {
                    if (input.type === "datetime-local" && input.value) {
                        input.dataset.datetimeValue = input.value;
                    }
                });
            });

            function applyMode() {
                if (weeklyToggle.checked) {
                    switchToTimeInput(startInput);
                    switchToTimeInput(endInput);
                } else {
                    switchToDatetimeInput(startInput);
                    switchToDatetimeInput(endInput);
                }
            }

            weeklyToggle.addEventListener("change", applyMode);
            applyMode();
        });
    }

    function initScheduleWindowInputMode() {
        var screens = [];
        var screenNode = document.getElementById("screen-data");
        if (screenNode) {
            try {
                screens = JSON.parse(screenNode.textContent || "[]");
            } catch (_err) {
                screens = [];
            }
        }
        function getScreenByIndex(index) {
            for (var i = 0; i < screens.length; i += 1) {
                if (Number(screens[i].index) === Number(index)) return screens[i];
            }
            return screens[0] || { left: 0, top: 0, width: 1920, height: 1080 };
        }
        function n(input, fallback) {
            var v = Number(input && input.value);
            return Number.isFinite(v) ? v : fallback;
        }

        document.querySelectorAll(".schedule-form").forEach(function (form) {
            var modeInput = form.querySelector("select[name='window_mode']");
            if (!modeInput) return;
            var screenInput = form.querySelector("select[name='screen_index']");
            var leftInput = form.querySelector("input[name='window_left']");
            var topInput = form.querySelector("input[name='window_top']");
            var widthInput = form.querySelector("input[name='window_width']");
            var heightInput = form.querySelector("input[name='window_height']");
            var visual = form.querySelector("[data-role='window-visual']");
            var screenBox = visual && visual.querySelector("[data-role='wv-screen']");
            var rect = visual && visual.querySelector("[data-role='wv-rect']");
            var handle = visual && visual.querySelector("[data-role='wv-handle']");
            var sizeLabel = visual && visual.querySelector("[data-role='wv-size']");
            if (!leftInput || !topInput || !widthInput || !heightInput) return;

            function getRectState() {
                var screen = getScreenByIndex(screenInput ? screenInput.value : 0);
                var relLeft = n(leftInput, screen.left) - Number(screen.left || 0);
                var relTop = n(topInput, screen.top) - Number(screen.top || 0);
                var w = Math.max(100, n(widthInput, Math.round(screen.width * 0.7)));
                var h = Math.max(100, n(heightInput, Math.round(screen.height * 0.7)));
                w = Math.min(w, Math.max(100, Number(screen.width || 1920)));
                h = Math.min(h, Math.max(100, Number(screen.height || 1080)));
                relLeft = Math.max(0, Math.min(relLeft, Number(screen.width || 1920) - w));
                relTop = Math.max(0, Math.min(relTop, Number(screen.height || 1080) - h));
                return { screen: screen, left: relLeft, top: relTop, width: w, height: h };
            }

            function setState(state) {
                leftInput.value = Math.round(Number(state.screen.left || 0) + state.left);
                topInput.value = Math.round(Number(state.screen.top || 0) + state.top);
                widthInput.value = Math.round(state.width);
                heightInput.value = Math.round(state.height);
            }

            function renderVisual() {
                if (!visual || !screenBox || !rect) return;
                var state = getRectState();
                var sw = Math.max(1, Number(state.screen.width || 1920));
                var sh = Math.max(1, Number(state.screen.height || 1080));
                var isCustom = modeInput.value === "custom";
                screenBox.style.aspectRatio = sw + " / " + sh;
                if (!isCustom) {
                    rect.style.left = "0%";
                    rect.style.top = "0%";
                    rect.style.width = "100%";
                    rect.style.height = "100%";
                    if (sizeLabel) sizeLabel.textContent = "FULL";
                    return;
                }
                rect.style.left = (state.left / sw) * 100 + "%";
                rect.style.top = (state.top / sh) * 100 + "%";
                rect.style.width = (state.width / sw) * 100 + "%";
                rect.style.height = (state.height / sh) * 100 + "%";
                if (sizeLabel) sizeLabel.textContent = Math.round(state.width) + " x " + Math.round(state.height);
            }

            function applyPreset(name) {
                var state = getRectState();
                var sw = Math.max(1, Number(state.screen.width || 1920));
                var sh = Math.max(1, Number(state.screen.height || 1080));
                if (name === "full") {
                    state.left = 0; state.top = 0; state.width = sw; state.height = sh;
                } else if (name === "left-half") {
                    state.left = 0; state.top = 0; state.width = Math.round(sw / 2); state.height = sh;
                } else if (name === "right-half") {
                    state.left = Math.round(sw / 2); state.top = 0; state.width = Math.round(sw / 2); state.height = sh;
                } else {
                    state.width = Math.round(sw * 0.8);
                    state.height = Math.round(sh * 0.8);
                    state.left = Math.round((sw - state.width) / 2);
                    state.top = Math.round((sh - state.height) / 2);
                }
                setState(state);
                renderVisual();
            }

            function applyMode() {
                var isCustom = modeInput.value === "custom";
                [leftInput, topInput, widthInput, heightInput].forEach(function (input) {
                    input.disabled = !isCustom;
                });
                if (visual) visual.classList.toggle("is-fullscreen", !isCustom);
            }

            if (visual && rect) {
                var dragState = null;
                function ensureCustomMode() {
                    if (modeInput.value === "custom") return;
                    modeInput.value = "custom";
                    applyMode();
                    renderVisual();
                }
                function beginDrag(type, ev) {
                    ev.preventDefault();
                    var state = getRectState();
                    dragState = {
                        type: type,
                        sx: ev.clientX,
                        sy: ev.clientY,
                        left: state.left,
                        top: state.top,
                        width: state.width,
                        height: state.height,
                        screen: state.screen
                    };
                }
                rect.addEventListener("mousedown", function (ev) {
                    if (ev.target === handle) return;
                    ensureCustomMode();
                    beginDrag("move", ev);
                });
                if (handle) {
                    handle.addEventListener("mousedown", function (ev) {
                        ev.stopPropagation();
                        ensureCustomMode();
                        beginDrag("resize", ev);
                    });
                }
                rect.addEventListener("pointerdown", function (ev) {
                    if (ev.target === handle) return;
                    ensureCustomMode();
                    beginDrag("move", ev);
                });
                if (handle) {
                    handle.addEventListener("pointerdown", function (ev) {
                        ev.stopPropagation();
                        ensureCustomMode();
                        beginDrag("resize", ev);
                    });
                }
                document.addEventListener("mousemove", function (ev) {
                    if (!dragState) return;
                    var sw = Math.max(1, Number(dragState.screen.width || 1920));
                    var sh = Math.max(1, Number(dragState.screen.height || 1080));
                    var box = screenBox.getBoundingClientRect();
                    var pxToW = sw / Math.max(1, box.width);
                    var pxToH = sh / Math.max(1, box.height);
                    var dx = (ev.clientX - dragState.sx) * pxToW;
                    var dy = (ev.clientY - dragState.sy) * pxToH;
                    var s = {
                        screen: dragState.screen,
                        left: dragState.left,
                        top: dragState.top,
                        width: dragState.width,
                        height: dragState.height
                    };
                    if (dragState.type === "move") {
                        s.left = Math.max(0, Math.min(dragState.left + dx, sw - s.width));
                        s.top = Math.max(0, Math.min(dragState.top + dy, sh - s.height));
                    } else {
                        s.width = Math.max(100, Math.min(dragState.width + dx, sw - s.left));
                        s.height = Math.max(100, Math.min(dragState.height + dy, sh - s.top));
                    }
                    setState(s);
                    renderVisual();
                });
                document.addEventListener("pointermove", function (ev) {
                    if (!dragState) return;
                    var sw = Math.max(1, Number(dragState.screen.width || 1920));
                    var sh = Math.max(1, Number(dragState.screen.height || 1080));
                    var box = screenBox.getBoundingClientRect();
                    var pxToW = sw / Math.max(1, box.width);
                    var pxToH = sh / Math.max(1, box.height);
                    var dx = (ev.clientX - dragState.sx) * pxToW;
                    var dy = (ev.clientY - dragState.sy) * pxToH;
                    var s = {
                        screen: dragState.screen,
                        left: dragState.left,
                        top: dragState.top,
                        width: dragState.width,
                        height: dragState.height
                    };
                    if (dragState.type === "move") {
                        s.left = Math.max(0, Math.min(dragState.left + dx, sw - s.width));
                        s.top = Math.max(0, Math.min(dragState.top + dy, sh - s.height));
                    } else {
                        s.width = Math.max(100, Math.min(dragState.width + dx, sw - s.left));
                        s.height = Math.max(100, Math.min(dragState.height + dy, sh - s.top));
                    }
                    setState(s);
                    renderVisual();
                });
                document.addEventListener("mouseup", function () {
                    dragState = null;
                });
                document.addEventListener("pointerup", function () {
                    dragState = null;
                });
                visual.querySelectorAll("[data-role='wv-preset']").forEach(function (btn) {
                    btn.addEventListener("click", function () {
                        var preset = btn.getAttribute("data-preset") || "center";
                        if (preset === "full") {
                            modeInput.value = "fullscreen";
                            applyMode();
                            renderVisual();
                            return;
                        }
                        if (modeInput.value !== "custom") modeInput.value = "custom";
                        applyMode();
                        applyPreset(preset);
                    });
                });
            }

            [leftInput, topInput, widthInput, heightInput].forEach(function (input) {
                input.addEventListener("input", renderVisual);
            });
            if (screenInput) screenInput.addEventListener("change", renderVisual);
            modeInput.addEventListener("change", function () {
                applyMode();
                renderVisual();
            });
            applyMode();
            renderVisual();
        });
    }

    if (document.querySelector("[data-role='player-state']")) {
        refreshStatus();
        window.setInterval(refreshStatus, 10000);
    }
    initGantt();
    initScheduleWeeklyInputMode();
    initScheduleWindowInputMode();
    initPlaylistEditor();
    initFileBrowser();
    initMonitorPolling();
    initNextcloudSettingsTools();
})();
