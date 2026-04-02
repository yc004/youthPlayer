(function () {
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

                setText("[data-role='player-state']", player.is_playing ? "播放中" : "空闲");
                setText("[data-role='screen-name']", player.screen_name || "未知屏幕");
                setText("[data-role='current-source']", player.current_source || "暂无播放内容");
                setText("[data-role='state-detail']", player.state || "Idle");
                setText("[data-role='backend']", player.backend || "idle");
                setText("[data-role='last-error']", player.last_error || "无");
                setText("[data-role='server-time']", payload.server_time || "--");
                setText("[data-role='active-schedule']", active ? active.name : "无");

                var badge = document.querySelector("[data-role='play-badge']");
                if (badge) {
                    badge.textContent = player.is_playing ? "播放中" : "待机";
                    badge.className = "chip " + (player.is_playing ? "success" : "muted");
                }
            } catch (err) {
                // 忽略自动刷新错误
            }
        };
        request.send();
    }

    if (document.querySelector("[data-role='player-state']")) {
        refreshStatus();
        window.setInterval(refreshStatus, 10000);
    }
})();
