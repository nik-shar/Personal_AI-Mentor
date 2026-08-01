/**
 * Personal AI Mentor — UI Application Logic
 * Integrates with FastAPI backend endpoints:
 * - POST /api/chat
 * - GET  /api/roadmaps
 * - GET  /api/roadmaps/{graph_id}
 * - GET  /api/nodes/detail
 * - GET  /api/profile
 */

let activeSessionId = null;
let visNetworkInstance = null;

// Initialize App
document.addEventListener("DOMContentLoaded", () => {
  fetchRoadmapsList();
  fetchProfileFacts();
});

// Tab Switching Navigation
function switchTab(tabId) {
  document.querySelectorAll(".nav-item").forEach((btn) => btn.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach((tab) => tab.classList.remove("active"));

  const targetBtn = document.getElementById(`nav-${tabId}-btn`);
  const targetTab = document.getElementById(`tab-${tabId}`);

  if (targetBtn) targetBtn.classList.add("active");
  if (targetTab) targetTab.classList.add("active");

  const subtitles = {
    chat: "Multi-Agent Orchestration & Real-time Goal Decomposition",
    graph: "Interactive Obsidian Topic Graph Visualizer",
    roadmaps: "Obsidian Vault Curriculum Index",
    profile: "Learner Identity, Goals & Habits",
  };

  const titles = {
    chat: "Interactive Mentor Chat",
    graph: "Obsidian Topic DAG Graph",
    roadmaps: "Obsidian Roadmaps",
    profile: "Learner Profile Facts",
  };

  document.getElementById("page-title").textContent = titles[tabId] || "AI Mentor";
  document.getElementById("page-subtitle").textContent = subtitles[tabId] || "";

  if (tabId === "graph") {
    loadSelectedGraph();
    setTimeout(() => {
      if (visNetworkInstance) {
        visNetworkInstance.redraw();
        visNetworkInstance.fit();
      }
    }, 150);
  } else if (tabId === "roadmaps") {
    fetchRoadmapsList();
  } else if (tabId === "profile") {
    fetchProfileFacts();
  }
}

// ---------------------------------------------------------------------------
// Chat Interface
// ---------------------------------------------------------------------------

function newSession() {
  activeSessionId = null;
  const thread = document.getElementById("chat-thread");
  thread.innerHTML = `
    <div class="message mentor-message">
      <div class="message-avatar">
        <i class="fa-solid fa-robot"></i>
      </div>
      <div class="message-content">
        <div class="sender-name">AI Mentor</div>
        <div class="message-body">
          Fresh mentor session started! Ask me anything or decompose a learning goal.
        </div>
      </div>
    </div>
  `;
}

function sendPrompt(text) {
  document.getElementById("chat-input").value = text;
  handleChatSubmit(new Event("submit"));
}

async function handleChatSubmit(event) {
  event.preventDefault();
  const inputEl = document.getElementById("chat-input");
  const rawValue = inputEl.value;
  const message = rawValue ? rawValue.trim() : "";

  if (!message) return;

  // Append user bubble
  appendUserMessage(message);
  inputEl.value = "";

  // Show typing indicator
  showTyping(true, "Reasoner & Router evaluating request...");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: message,
        session_id: activeSessionId,
      }),
    });

    if (!res.ok) {
      throw new Error(`HTTP error! status: ${res.status}`);
    }

    const data = await res.json();
    activeSessionId = data.session_id;

    showTyping(false);
    appendMentorMessage(data.response_text, data.agent_pipeline);

    // Refresh roadmaps list if goal decomposer was executed
    if (data.agent_pipeline.includes("goal_decomposer")) {
      fetchRoadmapsList();
    }
  } catch (err) {
    showTyping(false);
    appendMentorMessage(`⚠️ Communication Error: Unable to reach FastAPI backend (${err.message})`, []);
  }
}

function appendUserMessage(text) {
  const thread = document.getElementById("chat-thread");
  const msgHtml = `
    <div class="message user-message">
      <div class="message-avatar">
        <i class="fa-solid fa-user"></i>
      </div>
      <div class="message-content">
        <div class="sender-name">You</div>
        <div class="message-body">${escapeHtml(text)}</div>
      </div>
    </div>
  `;
  thread.insertAdjacentHTML("beforeend", msgHtml);
  thread.scrollTop = thread.scrollHeight;
}

function cleanResponseText(text) {
  if (!text) return "";
  let cleaned = text;
  cleaned = cleaned.replace(/\[USER REQUEST\][\s\S]*?The user requested:\s*"/g, '"');
  cleaned = cleaned.replace(/\[MENTOR GUIDANCE[\s\S]*?\[USER PROFILE[\s\S]*?\[PROCEDURAL RULES[\s\S]*?\]/g, "");
  return cleaned.trim();
}

function appendMentorMessage(text, pipeline) {
  const thread = document.getElementById("chat-thread");
  const cleanedText = cleanResponseText(text);

  // Parse Markdown using marked.js
  const parsedBody = typeof marked !== "undefined" ? marked.parse(cleanedText) : cleanedText;

  const msgHtml = `
    <div class="message mentor-message">
      <div class="message-avatar">
        <i class="fa-solid fa-robot"></i>
      </div>
      <div class="message-content">
        <div class="sender-name">AI Mentor</div>
        <div class="message-body markdown-content">${parsedBody}</div>
      </div>
    </div>
  `;
  thread.insertAdjacentHTML("beforeend", msgHtml);

  // Apply syntax highlighting
  if (typeof hljs !== "undefined") {
    thread.querySelectorAll("pre code").forEach((block) => hljs.highlightElement(block));
  }

  thread.scrollTop = thread.scrollHeight;
}

function showTyping(show, statusText = "Processing...") {
  const indicator = document.getElementById("typing-indicator");
  const statusEl = document.getElementById("agent-step-status");
  if (show) {
    indicator.classList.remove("hidden");
    if (statusEl) statusEl.textContent = statusText;
  } else {
    indicator.classList.add("hidden");
  }
}

// ---------------------------------------------------------------------------
// Obsidian Graph View (Vis.js Network)
// ---------------------------------------------------------------------------

async function loadSelectedGraph() {
  const select = document.getElementById("roadmap-select");
  if (!select) return;
  
  let graphId = select.value;
  if (!graphId && select.options && select.options.length > 0 && select.options[0].value) {
    select.selectedIndex = 0;
    graphId = select.options[0].value;
  }

  if (!graphId) return;

  try {
    const res = await fetch(`/api/roadmaps/${encodeURIComponent(graphId)}`);
    if (!res.ok) return;

    const data = await res.json();
    renderVisNetwork(data);
  } catch (err) {
    console.error("Error loading graph:", err);
  }
}

function renderVisNetwork(graphData) {
  const container = document.getElementById("graph-container");
  if (!container) return;
  container.innerHTML = "";

  if (!graphData || !graphData.nodes || !graphData.nodes.length) {
    container.innerHTML = `<div class="empty-state" style="padding:40px; text-align:center; color:#9CA3AF;">No topics found in this roadmap graph.</div>`;
    return;
  }

  const nodes = new vis.DataSet(
    graphData.nodes.map((n) => ({
      id: n.id,
      label: n.title,
      shape: "box",
      margin: 14,
      color: {
        background: n.status === "done" ? "#10B981" : "#1F2937",
        border: "#6366F1",
        highlight: { background: "#6366F1", border: "#A855F7" },
      },
      font: { color: "#FFFFFF", face: "Inter", size: 14, bold: true },
      shadow: true,
    }))
  );

  const edges = new vis.DataSet(
    graphData.edges.map((e) => ({
      from: e.from,
      to: e.to,
      arrows: "to",
      color: { color: "#64748B", highlight: "#6366F1" },
      width: 2,
    }))
  );

  const data = { nodes, edges };
  const options = {
    autoResize: true,
    height: "100%",
    width: "100%",
    physics: {
      solver: "forceAtlas2Based",
      forceAtlas2Based: {
        gravitationalConstant: -50,
        centralGravity: 0.01,
        springLength: 100,
        springConstant: 0.08,
      },
    },
    interaction: { hover: true, dragNodes: true, zoomView: true },
  };

  visNetworkInstance = new vis.Network(container, data, options);

  setTimeout(() => {
    if (visNetworkInstance) {
      visNetworkInstance.redraw();
      visNetworkInstance.fit();
    }
  }, 100);

  // Click node to open tutorial drawer
  visNetworkInstance.on("click", (params) => {
    if (params.nodes.length > 0) {
      const clickedNodeId = params.nodes[0];
      const clickedNode = graphData.nodes.find((n) => n.id === clickedNodeId);
      if (clickedNode) {
        openTutorialDrawer(clickedNode.title);
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Roadmaps List Tab
// ---------------------------------------------------------------------------

async function fetchRoadmapsList() {
  try {
    const res = await fetch("/api/roadmaps");
    if (!res.ok) return;

    const data = await res.json();
    const roadmaps = data.roadmaps || [];

    // Populate dropdown in Graph tab
    const select = document.getElementById("roadmap-select");
    if (select) {
      select.innerHTML = roadmaps.length
        ? roadmaps.map((r) => `<option value="${r.topic_id}">${escapeHtml(r.title)}</option>`).join("")
        : `<option value="">No roadmaps in vault</option>`;
      
      if (roadmaps.length > 0) {
        loadSelectedGraph();
      }
    }

    // Populate Grid in Roadmaps tab
    const grid = document.getElementById("roadmaps-grid");
    if (grid) {
      if (!roadmaps.length) {
        grid.innerHTML = `<div class="empty-state">No roadmaps found in Obsidian Vault yet. Say <i>'Create a 3-day roadmap for Rust'</i> in chat!</div>`;
        return;
      }

      grid.innerHTML = roadmaps
        .map(
          (r) => `
        <div class="roadmap-card" onclick="openRoadmapGraph('${r.topic_id}')">
          <h4><i class="fa-solid fa-map"></i> ${escapeHtml(r.title)}</h4>
          <div class="roadmap-meta">
            <span><i class="fa-solid fa-list-check"></i> ${r.completed}/${r.total_nodes} topics</span>
            <span><i class="fa-regular fa-clock"></i> ${r.total_hours}h total</span>
          </div>
          <button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); openRoadmapGraph('${r.topic_id}')">
            View Obsidian Graph
          </button>
        </div>
      `
        )
        .join("");
    }
  } catch (err) {
    console.error("Failed to fetch roadmaps:", err);
  }
}

function openRoadmapGraph(graphId) {
  const select = document.getElementById("roadmap-select");
  if (select) select.value = graphId;
  switchTab("graph");
}

// ---------------------------------------------------------------------------
// Slide-over Tutorial Drawer
// ---------------------------------------------------------------------------

async function openTutorialDrawer(title) {
  const drawer = document.getElementById("reader-drawer");
  const titleEl = document.getElementById("drawer-title");
  const bodyEl = document.getElementById("drawer-body");

  titleEl.textContent = title;
  bodyEl.innerHTML = `<div class="loading">Loading tutorial note from Obsidian Vault...</div>`;
  drawer.classList.remove("hidden");

  try {
    const res = await fetch(`/api/nodes/detail?filename=${encodeURIComponent(title)}`);
    if (!res.ok) {
      bodyEl.innerHTML = `<div class="error">Failed to load note '${escapeHtml(title)}' from vault.</div>`;
      return;
    }

    const data = await res.json();
    const parsedBody = typeof marked !== "undefined" ? marked.parse(data.body) : data.body;
    bodyEl.innerHTML = parsedBody;

    if (typeof hljs !== "undefined") {
      bodyEl.querySelectorAll("pre code").forEach((block) => hljs.highlightElement(block));
    }
  } catch (err) {
    bodyEl.innerHTML = `<div class="error">Error loading file: ${err.message}</div>`;
  }
}

function closeDrawer(event) {
  document.getElementById("reader-drawer").classList.add("hidden");
}

// ---------------------------------------------------------------------------
// Profile Facts Tab
// ---------------------------------------------------------------------------

async function fetchProfileFacts() {
  try {
    const res = await fetch("/api/profile");
    if (!res.ok) return;

    const data = await res.json();
    const profile = data.profile || {};
    const el = document.getElementById("profile-details");

    if (el) {
      el.innerHTML = `<pre><code>${JSON.stringify(profile, null, 2)}</code></pre>`;
      if (typeof hljs !== "undefined") {
        el.querySelectorAll("pre code").forEach((b) => hljs.highlightElement(b));
      }
    }
  } catch (err) {
    console.error("Failed to fetch profile facts:", err);
  }
}

// Helper: Escape HTML
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
