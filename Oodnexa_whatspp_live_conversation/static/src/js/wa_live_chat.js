/** @odoo-module **/

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { createFileViewer } from "@web/core/file_viewer/file_viewer_hook";

/**
 * Handle document, video, and image popup preview with offline download capability
 */
document.addEventListener("click", (e) => {
    const previewEl = e.target.closest(".wa-attachment-preview");
    if (!previewEl) return;
    e.preventDefault();
    e.stopPropagation();

    const attId = parseInt(previewEl.getAttribute("data-att-id"), 10);
    const name = previewEl.getAttribute("data-name") || "attachment";
    const mimetype = (previewEl.getAttribute("data-mimetype") || "").toLowerCase();

    const isImage = mimetype.startsWith("image/");
    const isVideo = mimetype.startsWith("video/");
    const isPdf = mimetype === "application/pdf";
    const isText = mimetype.startsWith("text/");

    const file = {
        id: attId,
        name: name,
        downloadUrl: `/web/content/${attId}?download=true`,
        defaultSource: `/web/content/${attId}`,
        mimetype: mimetype,
        isImage,
        isVideo,
        isPdf,
        isText,
        isViewable: isImage || isVideo || isPdf || isText,
    };

    try {
        const fileViewer = createFileViewer();
        fileViewer.open(file, [file]);
    } catch (err) {
        console.warn("[WhatsApp Live] FileViewer error, opening directly:", err);
        window.open(`/web/content/${attId}`, "_blank");
    }
});

/**
 * -------------------------------------------------------------
 * Voice Message / Audio Note Recorder (HTML5 MediaRecorder API)
 * -------------------------------------------------------------
 */
let mediaRecorder = null;
let audioChunks = [];
let recordingStream = null;
let recordingInterval = null;
let recordingSeconds = 0;
let activeRecordingMime = "audio/ogg";
let currentConvId = null;

function getSupportedAudioMime() {
    if (typeof MediaRecorder === "undefined") return "";
    // Priority:
    // 1. audio/ogg;codecs=opus (native in Firefox & supported browsers - direct Meta voice note)
    // 2. audio/ogg
    // 3. audio/webm;codecs=opus (native in Chrome / Edge / Opera / Android - pure Opus codec)
    // 4. audio/webm
    // 5. audio/mp4 (fallback only if nothing else supported)
    if (MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")) return "audio/ogg;codecs=opus";
    if (MediaRecorder.isTypeSupported("audio/ogg")) return "audio/ogg";
    if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) return "audio/webm;codecs=opus";
    if (MediaRecorder.isTypeSupported("audio/webm")) return "audio/webm";
    if (MediaRecorder.isTypeSupported("audio/mp4")) return "audio/mp4";
    return "";
}

function stopAndResetRecording() {
    clearInterval(recordingInterval);
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        try {
            mediaRecorder.stop();
        } catch {}
    }
    if (recordingStream) {
        recordingStream.getTracks().forEach((track) => track.stop());
        recordingStream = null;
    }
    mediaRecorder = null;
    audioChunks = [];
    recordingSeconds = 0;

    const idleEl = document.getElementById("wa-voice-idle-state");
    const recEl = document.getElementById("wa-voice-recording-state");
    if (recEl) recEl.style.setProperty("display", "none", "important");
    if (idleEl) idleEl.style.setProperty("display", "flex", "important");

    // Reset toolbar trigger buttons
    document.querySelectorAll(".wa-trigger-voice-rec").forEach((btn) => {
        btn.classList.remove("btn-danger");
        btn.classList.add("btn-outline-success");
        btn.innerHTML = '<i class="fa fa-microphone text-danger me-1"></i>Record Voice Message';
    });
}

// Start voice recording (Direct handler for BOTH upper and toolbar buttons)
document.addEventListener("click", async (e) => {
    if (e.target.closest(".wa-live-console-root")) return;
    const startBtn = e.target.closest("#wa-start-voice-rec-btn, .wa-trigger-voice-rec");
    if (!startBtn) return;
    e.preventDefault();
    e.stopPropagation();

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        alert("Microphone recording is not supported by your browser or connection is not secure (requires HTTPS or localhost).");
        return;
    }

    // Capture conversation ID from DOM
    currentConvId = parseInt(
        startBtn.getAttribute("data-conv-id") ||
        document.getElementById("wa-start-voice-rec-btn")?.getAttribute("data-conv-id") ||
        document.getElementById("wa-send-voice-rec-btn")?.getAttribute("data-conv-id") ||
        0, 10
    );

    try {
        recordingStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                channelCount: 1,
                echoCancellation: true,
                noiseSuppression: true,
            },
        });
    } catch (err) {
        alert("Microphone permission was denied. Please allow microphone access in your browser to record voice messages.");
        return;
    }

    const mime = getSupportedAudioMime();
    activeRecordingMime = mime || "audio/ogg";

    try {
        mediaRecorder = new MediaRecorder(recordingStream, mime ? { mimeType: mime } : undefined);
    } catch (recErr) {
        console.warn("[WhatsApp Live] Failed with chosen mime, falling back to default:", recErr);
        mediaRecorder = new MediaRecorder(recordingStream);
        activeRecordingMime = mediaRecorder.mimeType || "audio/ogg";
    }

    audioChunks = [];
    mediaRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
            audioChunks.push(event.data);
        }
    };

    recordingSeconds = 0;
    const idleEl = document.getElementById("wa-voice-idle-state");
    const recEl = document.getElementById("wa-voice-recording-state");
    const timerEl = document.getElementById("wa-voice-rec-timer");

    if (idleEl) idleEl.style.setProperty("display", "none", "important");
    if (recEl) {
        recEl.style.setProperty("display", "flex", "important");
        recEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    if (timerEl) timerEl.textContent = "00:00";

    // Update toolbar button to show active recording state
    document.querySelectorAll(".wa-trigger-voice-rec").forEach((btn) => {
        btn.classList.remove("btn-outline-success");
        btn.classList.add("btn-danger");
        btn.innerHTML = '<i class="fa fa-circle text-white me-1"></i>Recording Active...';
    });

    clearInterval(recordingInterval);
    recordingInterval = setInterval(() => {
        recordingSeconds++;
        const mins = String(Math.floor(recordingSeconds / 60)).padStart(2, "0");
        const secs = String(recordingSeconds % 60).padStart(2, "0");
        if (timerEl) timerEl.textContent = `${mins}:${secs}`;
    }, 1000);

    mediaRecorder.start(250);
});

// Cancel voice recording
document.addEventListener("click", (e) => {
    if (e.target.closest(".wa-live-console-root")) return;
    const cancelBtn = e.target.closest("#wa-cancel-voice-rec-btn");
    if (!cancelBtn) return;
    e.preventDefault();
    e.stopPropagation();
    stopAndResetRecording();
});

// Send voice recording
document.addEventListener("click", async (e) => {
    if (e.target.closest(".wa-live-console-root")) return;
    const sendBtn = e.target.closest("#wa-send-voice-rec-btn");
    if (!sendBtn) return;
    e.preventDefault();
    e.stopPropagation();

    if (!mediaRecorder || mediaRecorder.state === "inactive") {
        return;
    }

    const convId = currentConvId || parseInt(sendBtn.getAttribute("data-conv-id") || sendBtn.dataset.convId, 10);
    const durationSec = recordingSeconds;
    clearInterval(recordingInterval);

    sendBtn.disabled = true;
    sendBtn.innerHTML = '<i class="fa fa-spinner fa-spin me-1"></i>Sending...';

    mediaRecorder.onstop = async () => {
        if (recordingStream) {
            recordingStream.getTracks().forEach((track) => track.stop());
            recordingStream = null;
        }

        const blobMime = activeRecordingMime.split(";")[0] || "audio/ogg";
        const audioBlob = new Blob(audioChunks, { type: blobMime });

        // Convert Blob to Base64
        const reader = new FileReader();
        reader.readAsDataURL(audioBlob);
        reader.onloadend = async () => {
            const base64Data = (reader.result || "").split(",")[1];
            if (!base64Data) {
                alert("Failed to process recorded audio.");
                sendBtn.disabled = false;
                sendBtn.innerHTML = '<i class="fa fa-paper-plane me-1"></i>Send Voice Note';
                stopAndResetRecording();
                return;
            }

            try {
                const res = await rpc("/whatsapp_automation/conversation/send_voice_note", {
                    conv_id: convId,
                    audio_base64: base64Data,
                    mime_type: blobMime,
                    duration_sec: durationSec,
                });

                if (res && res.error) {
                    alert("Failed to send voice note to WhatsApp:\n" + res.error);
                } else {
                    playIncomingChime();
                    // Instantly refresh upper chat history
                    const chatBox = document.getElementById("wa_chat_history_box");
                    if (chatBox) {
                        const data = await rpc(`/whatsapp_automation/conversation/stream/${convId}`);
                        if (data && data.chat_history_html) {
                            const parser = new DOMParser();
                            const doc = parser.parseFromString(data.chat_history_html, "text/html");
                            const newBox = doc.getElementById("wa_chat_history_box");
                            if (newBox) {
                                chatBox.innerHTML = newBox.innerHTML;
                                scrollChatToBottom(chatBox);
                            }
                        }
                    }
                }
            } catch (err) {
                console.error("[WhatsApp Live] Error sending voice note:", err);
                alert("Error sending voice note: " + (err.message || err));
            } finally {
                sendBtn.disabled = false;
                sendBtn.innerHTML = '<i class="fa fa-paper-plane me-1"></i>Send Voice Note';
                stopAndResetRecording();
            }
        };
    };

    mediaRecorder.stop();
});


/**
 * Play a gentle WhatsApp-style audio chime using Web Audio API
 */
function playIncomingChime() {
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        const now = ctx.currentTime;

        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type = "sine";
        // Two-tone chime: D5 (587.33 Hz) -> A5 (880 Hz)
        osc.frequency.setValueAtTime(587.33, now);
        osc.frequency.exponentialRampToValueAtTime(880, now + 0.12);

        gain.gain.setValueAtTime(0.2, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.35);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(now);
        osc.stop(now + 0.35);
    } catch {
        // Ignored if user hasn't interacted with document yet
    }
}

/**
 * Scroll chat box smoothly to bottom
 */
function scrollChatToBottom(el) {
    if (el) {
        setTimeout(() => {
            el.scrollTop = el.scrollHeight;
        }, 60);
    }
}

/**
 * Insert WhatsApp markdown formatting around selected text in reply textarea
 */
export function applyWaFormatting(formatType) {
    const textarea = document.querySelector("textarea[name='current_reply_text']");
    if (!textarea) return;

    textarea.focus();
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const text = textarea.value;
    const selected = text.substring(start, end);

    let prefix = "*";
    let suffix = "*";
    let defaultSample = "bold text";

    if (formatType === "italic") {
        prefix = "_";
        suffix = "_";
        defaultSample = "italic text";
    } else if (formatType === "strike") {
        prefix = "~";
        suffix = "~";
        defaultSample = "strikethrough text";
    } else if (formatType === "code") {
        prefix = "`";
        suffix = "`";
        defaultSample = "code text";
    }

    let replacement = "";
    if (selected) {
        replacement = `${prefix}${selected}${suffix}`;
    } else {
        replacement = `${prefix}${defaultSample}${suffix}`;
    }

    const before = text.substring(0, start);
    const after = text.substring(end);
    textarea.value = before + replacement + after;

    // Set cursor position inside or after
    const newCursor = start + replacement.length;
    textarea.setSelectionRange(newCursor, newCursor);

    // Trigger standard input/change events so Odoo ORM detects the update
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new Event("change", { bubbles: true }));
}

// Global click handler for formatting buttons
document.addEventListener("click", (e) => {
    const btn = e.target.closest(".wa-format-btn");
    if (btn) {
        e.preventDefault();
        const type = btn.getAttribute("data-format");
        if (type) {
            applyWaFormatting(type);
        }
    }
});

/**
 * WhatsApp Live Chat Service
 * - Handles bus notifications from Odoo WebSocket
 * - Periodically syncs chat stream without full page reload
 */
export const waLiveChatService = {
    dependencies: ["bus_service", "notification", "action"],
    start(env, { bus_service, notification, action }) {
        let isRefreshing = false;

        async function refreshChatStream(convId, playSound = false) {
            if (isRefreshing) return;
            const chatBox = document.getElementById("wa_chat_history_box");
            if (!chatBox) return;

            const openId = parseInt(chatBox.getAttribute("data-conversation-id"), 10);
            if (openId !== convId) return;

            isRefreshing = true;
            try {
                const data = await rpc(`/whatsapp_automation/conversation/stream/${convId}`);
                if (data && data.chat_history_html) {
                    const currentBox = document.getElementById("wa_chat_history_box");
                    if (currentBox) {
                        // Parse received HTML safely and only update inner content
                        // NEVER touch currentBox.outerHTML to avoid corrupting Owl's VHtml DOM node tracking
                        const parser = new DOMParser();
                        const doc = parser.parseFromString(data.chat_history_html, "text/html");
                        const newBox = doc.getElementById("wa_chat_history_box");
                        if (newBox) {
                            currentBox.innerHTML = newBox.innerHTML;
                            currentBox.setAttribute(
                                "data-message-count",
                                newBox.getAttribute("data-message-count") || data.message_count
                            );
                            if (newBox.getAttribute("style")) {
                                currentBox.setAttribute("style", newBox.getAttribute("style"));
                            }
                            scrollChatToBottom(currentBox);
                            if (playSound) {
                                playIncomingChime();
                            }
                        }
                    }
                }
            } catch (err) {
                console.warn("[WhatsApp Live] Stream refresh error:", err);
            } finally {
                isRefreshing = false;
            }
        }

        // 1. Subscribe to WebSocket / Bus Notifications
        bus_service.subscribe("whatsapp.conversation/new_message", (payload) => {
            const chatBox = document.getElementById("wa_chat_history_box");
            const openId = chatBox ? parseInt(chatBox.getAttribute("data-conversation-id"), 10) : null;

            if (chatBox && openId === payload.conversation_id) {
                // Instantly update the chat stream container
                refreshChatStream(payload.conversation_id, payload.direction === "incoming");
            } else {
                // User is on another screen or list view: show notification
                notification.add(
                    `${payload.sender_name || "Customer"}: ${payload.body || "New WhatsApp message"}`,
                    {
                        title: "WhatsApp Message Received",
                        type: "info",
                        sticky: false,
                    }
                );
                playIncomingChime();

                // If currently viewing whatsapp conversation list/kanban, reload list state
                const curr = action.currentController;
                if (curr && curr.action && curr.action.res_model === "whatsapp.automation.conversation") {
                    action.loadState();
                }
            }
        });

        // 2. Resilient Poller: auto-check active chat every 4 seconds
        setInterval(async () => {
            const chatBox = document.getElementById("wa_chat_history_box");
            if (!chatBox || isRefreshing) return;

            const convId = parseInt(chatBox.getAttribute("data-conversation-id"), 10);
            const currentCount = parseInt(chatBox.getAttribute("data-message-count"), 10);
            if (!convId) return;

            try {
                const data = await rpc(`/whatsapp_automation/conversation/stream/${convId}`);
                if (data && typeof data.message_count === "number" && data.message_count > currentCount) {
                    await refreshChatStream(convId, true);
                }
            } catch {
                // Silently ignore background poll errors
            }
        }, 4000);

        // 3. Keyboard Shortcut: Ctrl + Enter or Cmd + Enter to Send WhatsApp message
        document.addEventListener("keydown", (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                const textarea = document.querySelector("textarea[name='current_reply_text']");
                if (textarea && document.activeElement === textarea) {
                    const sendBtn = document.querySelector("button[name='action_send_reply']");
                    if (sendBtn) {
                        e.preventDefault();
                        sendBtn.click();
                    }
                }
            }
        });

        // 4. Ensure chat box is scrolled to bottom on initial view
        let lastObservedBox = null;
        setInterval(() => {
            const chatBox = document.getElementById("wa_chat_history_box");
            if (chatBox && chatBox !== lastObservedBox) {
                lastObservedBox = chatBox;
                scrollChatToBottom(chatBox);
            }
        }, 500);
    },
};

registry.category("services").add("wa_live_chat_service", waLiveChatService);
