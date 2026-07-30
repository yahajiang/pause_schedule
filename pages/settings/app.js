const bridge = window.AstrBotPluginPage;

function showToast(msg, duration = 2500) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), duration);
}

function getConfig() {
    return {
        enabled: document.getElementById("enabled").checked,
        pause_start: document.getElementById("pauseStart").value || "23:00",
        pause_end: document.getElementById("pauseEnd").value || "08:00",
        pause_message: document.getElementById("pauseMessage").value,
        admin_only: document.getElementById("adminOnly").checked,
        exclude_admin: document.getElementById("excludeAdmin").checked,
        notify_on_pause: document.getElementById("notifyOnPause").checked,
        timezone: document.getElementById("timezone").value,
    };
}

function setConfig(cfg) {
    if (!cfg) return;
    document.getElementById("enabled").checked = cfg.enabled !== false;
    document.getElementById("pauseStart").value = cfg.pause_start || "23:00";
    document.getElementById("pauseEnd").value = cfg.pause_end || "08:00";
    document.getElementById("pauseMessage").value = cfg.pause_message || "";
    document.getElementById("adminOnly").checked = cfg.admin_only !== false;
    document.getElementById("excludeAdmin").checked = cfg.exclude_admin !== false;
    document.getElementById("notifyOnPause").checked = cfg.notify_on_pause === true;
    document.getElementById("timezone").value = cfg.timezone || "Asia/Shanghai";
}

async function loadConfig() {
    try {
        const res = await bridge.apiGet("config");
        if (res && res.code === 0 && res.data) {
            setConfig(res.data);
        }
    } catch (e) {
        console.error("加载配置失败:", e);
    }
}

async function saveConfig() {
    try {
        const cfg = getConfig();
        const res = await bridge.apiPost("config", cfg);
        if (res && res.code === 0) {
            showToast("✅ 配置已保存");
        } else {
            showToast("❌ 保存失败: " + (res?.msg || "未知错误"));
        }
    } catch (e) {
        showToast("❌ 保存失败: " + e.message);
    }
}

async function refreshStatus() {
    try {
        const res = await bridge.apiGet("status");
        if (res && res.code === 0 && res.data) {
            const d = res.data;
            const bar = document.getElementById("statusBar");
            const text = document.getElementById("statusText");
            const timeEl = document.getElementById("currentTime");

            if (d.paused) {
                bar.className = "status-bar paused";
                text.textContent = "⏸️ 暂停中";
            } else {
                bar.className = "status-bar running";
                text.textContent = "▶️ 运行中";
            }
            timeEl.textContent = d.current_time || "--:--:--";
        }
    } catch (e) {
        // 静默失败
    }
}

document.getElementById("saveBtn").addEventListener("click", saveConfig);

// 初始化
(async function () {
    await bridge.ready();
    await loadConfig();
    refreshStatus();
    setInterval(refreshStatus, 10000);
})();
