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

                setText("[data-role='player-state']", player.is_playing ? "播放中" : "空闲");
                setText("[data-role='screen-name']", player.screen_name || "未知屏幕");
                setText("[data-role='current-source']", player.current_source || "暂无播放内容");
                setText("[data-role='state-detail']", player.state || "Idle");
                setText("[data-role='backend']", player.backend || "idle");
                setText("[data-role='last-error']", player.last_error || "无");
                setText("[data-role='server-time']", payload.server_time || "--");
                setText("[data-role='active-schedule']", active ? active.name : "无");
                setText("[data-role='monitor-time']", monitor.captured_at || "等待截图");

                var monitorImg = document.querySelector("[data-role='monitor-preview']");
                if (monitorImg && monitor.frame_url) {
                    monitorImg.src = monitor.frame_url + "?t=" + Date.now();
                }

                var badge = document.querySelector("[data-role='play-badge']");
                if (badge) {
                    badge.textContent = player.is_playing ? "播放中" : "待机";
                    badge.className = "chip " + (player.is_playing ? "success" : "muted");
                }
            } catch (_err) {
                // ignore
            }
        };
        request.send();
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
        var currentPath = "";
        var currentParent = "";
        var activeInput = null;

        function closeBrowser() {
            backdrop.hidden = true;
            activeInput = null;
        }

        function openBrowser(input) {
            activeInput = input;
            backdrop.hidden = false;
            loadPath(input.value || "");
        }

        function renderEntries(entries) {
            listNode.innerHTML = "";
            if (!entries.length) {
                var empty = document.createElement("div");
                empty.className = "empty-state";
                empty.textContent = "目录为空";
                listNode.appendChild(empty);
                return;
            }
            entries.forEach(function (item) {
                var row = document.createElement("div");
                row.className = "fb-row";

                var label = document.createElement("span");
                label.className = "fb-name" + (item.is_dir ? " dir" : " file");
                label.textContent = (item.is_dir ? "📁 " : "📄 ") + item.name;
                label.title = item.path;

                var action = document.createElement("button");
                action.type = "button";
                action.className = "btn btn-secondary";
                action.textContent = item.is_dir ? "打开" : "选择";
                action.addEventListener("click", function () {
                    if (item.is_dir) {
                        loadPath(item.path);
                    } else if (activeInput) {
                        activeInput.value = item.path;
                        closeBrowser();
                    }
                });

                row.appendChild(label);
                row.appendChild(action);
                listNode.appendChild(row);
            });
        }

        function loadPath(path) {
            var url = "/api/browse";
            if (path) {
                url += "?path=" + encodeURIComponent(path);
            }
            var request = new XMLHttpRequest();
            request.open("GET", url, true);
            request.onreadystatechange = function () {
                if (request.readyState !== 4) return;
                if (request.status !== 200) {
                    listNode.innerHTML = "<div class='empty-state'>路径访问失败</div>";
                    return;
                }
                try {
                    var payload = JSON.parse(request.responseText);
                    currentPath = payload.cwd || "";
                    currentParent = payload.parent || "";
                    pathInput.value = currentPath;
                    cwdNode.textContent = "当前位置：" + (currentPath || "盘符列表");
                    renderEntries(payload.entries || []);
                } catch (_err) {
                    listNode.innerHTML = "<div class='empty-state'>数据解析失败</div>";
                }
            };
            request.send();
        }

        document.querySelectorAll(".btn-path-browse").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var input = btn.closest(".path-picker").querySelector(".content-path-input");
                if (input) openBrowser(input);
            });
        });

        backdrop.querySelector("[data-role='fb-close']").addEventListener("click", closeBrowser);
        backdrop.querySelector("[data-role='fb-roots']").addEventListener("click", function () {
            loadPath("");
        });
        backdrop.querySelector("[data-role='fb-up']").addEventListener("click", function () {
            if (currentParent) {
                loadPath(currentParent);
            } else {
                loadPath("");
            }
        });
        backdrop.querySelector("[data-role='fb-go']").addEventListener("click", function () {
            loadPath(pathInput.value.trim());
        });
        backdrop.querySelector("[data-role='fb-use-dir']").addEventListener("click", function () {
            if (activeInput) {
                activeInput.value = currentPath;
                closeBrowser();
            }
        });
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

    function refreshMonitorOnly() {
        var monitorImg = document.querySelector("[data-role='monitor-preview']");
        if (!monitorImg) return;
        var request = new XMLHttpRequest();
        request.open("GET", "/api/monitor", true);
        request.onreadystatechange = function () {
            if (request.readyState !== 4 || request.status !== 200) return;
            try {
                var payload = JSON.parse(request.responseText);
                setText("[data-role='monitor-time']", payload.captured_at || "等待截图");
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
        if (!document.querySelector("[data-role='monitor-preview']")) return;
        refreshMonitorOnly();
        window.setInterval(refreshMonitorOnly, 5000);
    }

    if (document.querySelector("[data-role='player-state']")) {
        refreshStatus();
        window.setInterval(refreshStatus, 10000);
    }
    initGantt();
    initFileBrowser();
    initMonitorPolling();
})();
