from flask import Flask, render_template_string, jsonify
import json
import os
import time

app = Flask(__name__)

STATUS_FILE = os.environ.get(
    "DASHBOARD_STATUS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_status.json")
)

# Shared state
state = {
    "script_running": False,
    "last_attempt": 0,
    "total_attempts": 0,
    "current_ad": "N/A",
    "status": "waiting",  # waiting, trying, success, failed
    "message": "Dashboard started",
    "instance_details": None,
    "start_time": None,
    "last_update": None
}

def load_state():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r') as f:
                state.update(json.load(f))
        except:
            pass

def save_state():
    with open(STATUS_FILE, 'w') as f:
        json.dump(state, f)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Ampere A1 Instance Creator Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #e6edf3;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 800px; margin: 0 auto; }
        h1 { 
            color: #58a6ff; 
            margin-bottom: 24px; 
            font-size: 1.8rem;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.9rem;
        }
        .status-running { background: #1f6feb; color: #fff; }
        .status-success { background: #238636; color: #fff; }
        .status-failed { background: #da3633; color: #fff; }
        .status-waiting { background: #8b949e; color: #fff; }
        .card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 16px;
        }
        .card h2 {
            color: #8b949e;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 12px;
            font-weight: 600;
        }
        .metric-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
        }
        .metric {
            background: #0d1117;
            border: 1px solid #21262d;
            border-radius: 6px;
            padding: 16px;
        }
        .metric-label {
            color: #8b949e;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }
        .metric-value {
            color: #e6edf3;
            font-size: 1.5rem;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }
        .log-entry {
            background: #0d1117;
            border: 1px solid #21262d;
            border-radius: 6px;
            padding: 12px;
            margin-bottom: 8px;
            font-family: 'SF Mono', 'Fira Code', monospace;
            font-size: 0.85rem;
            line-height: 1.5;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .log-success { border-left: 3px solid #238636; }
        .log-warning { border-left: 3px solid #d29922; }
        .log-error { border-left: 3px solid #da3633; }
        .log-info { border-left: 3px solid #58a6ff; }
        .pulse {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #58a6ff;
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        .pulse-success { background: #3fb950; animation: none; }
        .pulse-failed { background: #f85149; animation: none; }
        .instance-info {
            background: #0d1117;
            border: 1px solid #21262d;
            border-radius: 6px;
            padding: 16px;
        }
        .instance-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #21262d;
        }
        .instance-row:last-child { border-bottom: none; }
        .instance-label { color: #8b949e; }
        .instance-value { color: #e6edf3; font-family: monospace; font-size: 0.9rem; }
        .refresh-indicator {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 0.8rem;
            color: #8b949e;
        }
        .refresh-indicator.active { border-color: #58a6ff; color: #58a6ff; }
    </style>
</head>
<body>
    <div class="container">
        <h1>
            <span class="pulse" id="pulse"></span>
            Ampere A1 Instance Creator
        </h1>
        
        <div class="card">
            <h2>Script Status</h2>
            <div class="metric-grid">
                <div class="metric">
                    <div class="metric-label">Status</div>
                    <div class="metric-value">
                        <span class="status-badge status-{{ state.status }}" id="statusBadge">{{ state.status|upper }}</span>
                    </div>
                </div>
                <div class="metric">
                    <div class="metric-label">Attempts</div>
                    <div class="metric-value" id="attempts">{{ state.total_attempts }}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Current AD</div>
                    <div class="metric-value" id="currentAd">{{ state.current_ad }}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Runtime</div>
                    <div class="metric-value" id="runtime">{{ state.runtime }}</div>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>Latest Activity</h2>
            <div id="logContainer">
                <div class="log-entry log-info">Dashboard started - waiting for script...</div>
            </div>
        </div>

        <div class="card" id="instanceCard" style="display: none;">
            <h2>Instance Details</h2>
            <div class="instance-info" id="instanceInfo"></div>
        </div>
    </div>

    <div class="refresh-indicator" id="refreshIndicator">Auto-refresh: ON</div>

    <script>
        async function fetchState() {
            try {
                const response = await fetch('/api/state');
                const data = await response.json();
                updateUI(data);
            } catch (e) {
                console.error('Fetch error:', e);
            }
        }

        function updateUI(data) {
            document.getElementById('statusBadge').textContent = data.status.toUpperCase();
            document.getElementById('statusBadge').className = 'status-badge status-' + data.status;
            document.getElementById('attempts').textContent = data.total_attempts;
            document.getElementById('currentAd').textContent = data.current_ad;
            document.getElementById('runtime').textContent = data.runtime;
            
            const pulse = document.getElementById('pulse');
            pulse.className = 'pulse';
            if (data.status === 'success') pulse.classList.add('pulse-success');
            if (data.status === 'failed') pulse.classList.add('pulse-failed');
            
            // Update logs
            const logContainer = document.getElementById('logContainer');
            if (data.logs && data.logs.length > 0) {
                logContainer.innerHTML = data.logs.map(log => 
                    `<div class="log-entry log-${log.type}">${escapeHtml(log.message)}</div>`
                ).join('');
            }
            
            // Show instance details on success
            const instanceCard = document.getElementById('instanceCard');
            if (data.status === 'success' && data.instance_details) {
                instanceCard.style.display = 'block';
                const inst = data.instance_details;
                // All values escaped - never inject API/instance data as raw HTML
                const row = (label, value) =>
                    `<div class="instance-row"><span class="instance-label">${escapeHtml(label)}</span><span class="instance-value">${escapeHtml(value)}</span></div>`;
                document.getElementById('instanceInfo').innerHTML =
                    row('Name', inst.instance_name) +
                    row('OCID', inst.instance_id) +
                    row('Shape', `${inst.shape} (${inst.ocpus} OCPU, ${inst.memory_gb} GB)`) +
                    row('AD', inst.availability_domain) +
                    row('Public IP', inst.public_ip || 'Pending...') +
                    row('Private IP', inst.private_ip || 'Pending...') +
                    row('SSH', `ssh -i <key> ubuntu@${inst.public_ip || inst.private_ip}`);
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Poll every 2 seconds
        setInterval(fetchState, 2000);
        fetchState();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    load_state()
    # Compute runtime
    if state['start_time']:
        state['runtime'] = format_duration(time.time() - state['start_time'])
    else:
        state['runtime'] = '0s'
    return render_template_string(HTML_TEMPLATE, state=state)

@app.route('/api/state')
def api_state():
    load_state()
    # Compute runtime
    if state['start_time']:
        state['runtime'] = format_duration(time.time() - state['start_time'])
    else:
        state['runtime'] = '0s'
    return jsonify(state)

def format_duration(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds//60)}m {int(seconds%60)}s"
    else:
        h = int(seconds//3600)
        m = int((seconds%3600)//60)
        return f"{h}h {m}m"

if __name__ == '__main__':
    # Bind to localhost only: this dashboard has no authentication and must
    # not be reachable from other machines. Use an SSH tunnel if remote
    # access is needed: ssh -L 5050:localhost:5050 user@host
    print("Starting dashboard on http://localhost:5050")
    app.run(host='127.0.0.1', port=5050, debug=False, threaded=True)
