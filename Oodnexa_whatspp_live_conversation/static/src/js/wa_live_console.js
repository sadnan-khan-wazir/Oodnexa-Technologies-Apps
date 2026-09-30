/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onMounted, onWillUnmount, useRef, markup } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { user } from "@web/core/user";

export class WaLiveConsole extends Component {
    static template = "Oodnexa_whatspp_live_conversation.LiveConsole";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.chatBoxRef = useRef("chatScrollContainer");
        this.fileInputRef = useRef("fileInput");

        this.mediaRecorder = null;
        this.audioChunks = [];
        this.recordingStream = null;
        this.recordingInterval = null;
        this.recordingSeconds = 0;
        this.activeRecordingMime = "audio/ogg";

        this.state = useState({
            conversations: [],
            filteredConversations: [],
            selectedConvId: null,
            selectedConv: null,
            activeTab: "all", // "all", "unread", "my"
            searchQuery: "",
            replyText: "",
            loading: true,
            chatLoading: false,
            sending: false,
            chatHistoryHtml: markup(""),
            auditMessages: [],
            totalUnreadCount: 0,
            selectedFiles: [],
            isAuditOpen: false,
            isRecording: false,
            recordingTimer: "00:00",
            isSendingVoice: false,
            mobilePane: typeof window !== "undefined" && window.innerWidth < 992 ? "list" : "chat",
            showMobileDetails: false,
        });

        onWillStart(async () => {
            await this.loadConversations();
        });

        onMounted(() => {
            this.scrollToBottom();
        });

        onWillUnmount(() => {
            if (this.state.isRecording) {
                this.cancelVoiceRecording();
            }
        });
    }

    async loadConversations() {
        try {
            const domain = [];
            const fields = [
                "id",
                "name",
                "contact_display_name",
                "contact_initials",
                "phone",
                "last_message_text",
                "last_message_at",
                "last_message_status",
                "unread_count",
                "partner_id",
                "has_sales_orders",
                "user_id",
                "provider_id",
                "company_id",
                "state",
            ];

            const records = await this.orm.searchRead(
                "whatsapp.automation.conversation",
                domain,
                fields,
                { order: "last_message_at desc, id desc", limit: 50 }
            );

            this.state.conversations = records;
            this.state.totalUnreadCount = records.reduce((acc, c) => acc + (c.unread_count || 0), 0);
            this.filterConversations();

            // Auto-select first conversation if none selected
            if (this.state.filteredConversations.length > 0) {
                const targetId = this.state.selectedConvId || this.state.filteredConversations[0].id;
                await this.selectConversation(targetId);
            } else {
                this.state.selectedConvId = null;
                this.state.selectedConv = null;
                this.state.chatHistoryHtml = markup("");
                this.state.auditMessages = [];
            }
            this.state.loading = false;
        } catch (err) {
            console.error("Error loading conversations:", err);
            this.state.loading = false;
        }
    }

    filterConversations() {
        let list = this.state.conversations;

        // Apply Tab Filter (Only All, Unread, My Messages)
        if (this.state.activeTab === "unread") {
            list = list.filter((c) => (c.unread_count || 0) > 0);
        } else if (this.state.activeTab === "my") {
            list = list.filter((c) => c.user_id && c.user_id[0] === user.userId);
        }

        // Apply Search Query
        if (this.state.searchQuery.trim()) {
            const q = this.state.searchQuery.trim().toLowerCase();
            list = list.filter(
                (c) =>
                    (c.contact_display_name || "").toLowerCase().includes(q) ||
                    (c.phone || "").toLowerCase().includes(q) ||
                    (c.last_message_text || "").toLowerCase().includes(q)
            );
        }

        this.state.filteredConversations = list;
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        this.filterConversations();
    }

    async setTab(tab) {
        this.state.activeTab = tab;
        this.filterConversations();
        if (this.state.filteredConversations.length > 0) {
            await this.selectConversation(this.state.filteredConversations[0].id);
        }
    }

    async selectConversation(convId, isUserClick = false) {
        if (this.state.isRecording) {
            this.cancelVoiceRecording();
        }
        this.clearAllAttachments();
        this.state.selectedConvId = convId;
        this.state.chatLoading = true;
        if (isUserClick || (typeof window !== "undefined" && window.innerWidth >= 992)) {
            this.state.mobilePane = "chat";
        }
        this.state.showMobileDetails = false;

        const conv = this.state.conversations.find((c) => c.id === convId);
        this.state.selectedConv = conv;

        try {
            // Read chat_history_html and details
            const [data] = await this.orm.read(
                "whatsapp.automation.conversation",
                [convId],
                [
                    "name",
                    "chat_history_html",
                    "partner_id",
                    "phone",
                    "provider_id",
                    "user_id",
                    "company_id",
                    "state",
                    "last_message_at",
                    "unread_count",
                    "provider_verified_name",
                    "provider_display_phone",
                    "provider_phone_number_id",
                    "provider_sandbox_mode",
                    "provider_quality_rating",
                    "provider_account_mode_label",
                ]
            );

            if (data) {
                this.state.selectedConv = Object.assign({}, conv, data);
                this.state.chatHistoryHtml = markup(data.chat_history_html || "");
            }

            // Read message audit log
            const auditMsgs = await this.orm.searchRead(
                "whatsapp.automation.message",
                [["conversation_id", "=", convId]],
                ["create_date", "direction", "rendered_body", "state", "provider_message_id"],
                { order: "create_date desc", limit: 20 }
            );
            this.state.auditMessages = auditMsgs;

            this.state.chatLoading = false;
            this.scrollToBottom();

            // Mark conversation as read if unread
            if (conv && conv.unread_count > 0) {
                await this.orm.call("whatsapp.automation.conversation", "action_mark_read", [[convId]]);
                conv.unread_count = 0;
                this.state.totalUnreadCount = this.state.conversations.reduce((acc, c) => acc + (c.unread_count || 0), 0);
            }
        } catch (err) {
            console.error("Error selecting conversation:", err);
            this.state.chatLoading = false;
        }
    }

    toggleAudit() {
        this.state.isAuditOpen = !this.state.isAuditOpen;
    }

    formatDateTime(dtStr) {
        if (!dtStr) return "-";
        try {
            const d = new Date(dtStr.replace(" ", "T"));
            return d.toLocaleString("en-US", {
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit",
                hour12: true,
            });
        } catch {
            return dtStr;
        }
    }

    scrollToBottom() {
        setTimeout(() => {
            if (this.chatBoxRef.el) {
                this.chatBoxRef.el.scrollTop = this.chatBoxRef.el.scrollHeight;
            }
        }, 100);
    }

    onComposerKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }

    onComposerInput(ev) {
        const el = ev.target;
        el.style.height = "auto";
        el.style.height = Math.min(Math.max(el.scrollHeight, 72), 220) + "px";
    }

    triggerFileInput() {
        if (this.fileInputRef.el) {
            this.fileInputRef.el.click();
        }
    }

    onFileChange(ev) {
        const files = Array.from(ev.target.files || []);
        if (!files.length) return;

        for (const file of files) {
            if (this.state.selectedFiles.some((f) => f.name === file.name && f.size === file.size)) {
                continue;
            }
            const sizeKb = Math.round(file.size / 1024);
            const sizeStr = sizeKb > 1024 ? `${(sizeKb / 1024).toFixed(1)} MB` : `${sizeKb} KB`;
            this.state.selectedFiles.push({
                file: file,
                name: file.name,
                size: file.size,
                sizeStr: sizeStr,
            });
        }

        if (this.fileInputRef.el) {
            this.fileInputRef.el.value = "";
        }
    }

    removeSelectedFile(index) {
        this.state.selectedFiles.splice(index, 1);
    }

    clearAllAttachments() {
        this.state.selectedFiles = [];
        if (this.fileInputRef.el) {
            this.fileInputRef.el.value = "";
        }
    }

    getFileIconClass(mime, name) {
        mime = (mime || "").toLowerCase();
        name = (name || "").toLowerCase();
        if (mime.startsWith("image/") || name.match(/\.(png|jpe?g|webp|gif|bmp)$/)) return "fa fa-file-image-o text-primary";
        if (mime.startsWith("video/") || name.match(/\.(mp4|3gp|mov|avi)$/)) return "fa fa-file-video-o text-danger";
        if (mime.startsWith("audio/") || name.match(/\.(mp3|ogg|wav|m4a|aac)$/)) return "fa fa-file-audio-o text-warning";
        if (mime === "application/pdf" || name.endsWith(".pdf")) return "fa fa-file-pdf-o text-danger";
        if (name.match(/\.(xls|xlsx|csv)$/)) return "fa fa-file-excel-o text-success";
        if (name.match(/\.(doc|docx)$/)) return "fa fa-file-word-o text-primary";
        if (name.match(/\.(ppt|pptx)$/)) return "fa fa-file-powerpoint-o text-danger";
        return "fa fa-file-text-o text-secondary";
    }

    readFileAsBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const res = (reader.result || "").split(",")[1];
                resolve(res);
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    async sendMessage() {
        const text = (this.state.replyText || "").trim();
        const hasFiles = this.state.selectedFiles && this.state.selectedFiles.length > 0;
        if ((!text && !hasFiles) || !this.state.selectedConvId || this.state.sending) return;

        this.state.sending = true;
        try {
            const vals = { current_reply_text: text };

            if (hasFiles) {
                const attIds = [];
                for (const item of this.state.selectedFiles) {
                    const file = item.file;
                    const base64 = await this.readFileAsBase64(file);

                    // Detect or fall back MIME type from file extension
                    let fileMime = file.type || "";
                    if (!fileMime || fileMime === "application/octet-stream") {
                        const ext = (file.name || "").split(".").pop().toLowerCase();
                        const extMap = {
                            pdf: "application/pdf",
                            png: "image/png",
                            jpg: "image/jpeg",
                            jpeg: "image/jpeg",
                            webp: "image/webp",
                            mp4: "video/mp4",
                            "3gp": "video/3gpp",
                            mp3: "audio/mpeg",
                            ogg: "audio/ogg",
                            doc: "application/msword",
                            docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            xls: "application/vnd.ms-excel",
                            xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            ppt: "application/vnd.ms-powerpoint",
                            pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            txt: "text/plain",
                        };
                        fileMime = extMap[ext] || "application/pdf";
                    }

                    const attRes = await this.orm.create("ir.attachment", [{
                        name: file.name,
                        datas: base64,
                        res_model: "whatsapp.automation.conversation",
                        res_id: this.state.selectedConvId,
                        mimetype: fileMime,
                    }]);

                    const attId = Array.isArray(attRes) ? attRes[0] : attRes;
                    attIds.push(attId);
                }

                vals.reply_attachment_ids = attIds.map((id) => [4, id]);
            }

            await this.orm.write("whatsapp.automation.conversation", [this.state.selectedConvId], vals);
            await this.orm.call("whatsapp.automation.conversation", "action_send_reply", [[this.state.selectedConvId]]);

            this.state.replyText = "";
            this.clearAllAttachments();
            this.state.sending = false;

            const input = document.getElementById("wa_console_reply_input");
            if (input) {
                input.style.height = "auto";
            }

            // Refresh current conversation & chat stream
            await this.selectConversation(this.state.selectedConvId);
            await this.loadConversations();
        } catch (err) {
            this.state.sending = false;
            console.error("Error sending WhatsApp message/attachment:", err);
            const errorMsg = err.data?.message || err.message || "Failed to send message";
            this.notification.add(errorMsg, { type: "danger" });
        }
    }

    applyFormat(format) {
        const input = document.getElementById("wa_console_reply_input");
        if (!input) return;
        const start = input.selectionStart;
        const end = input.selectionEnd;
        const val = this.state.replyText || "";
        const selected = val.substring(start, end) || "text";

        let formatted = selected;
        if (format === "bold") formatted = `*${selected}*`;
        else if (format === "italic") formatted = `_${selected}_`;
        else if (format === "strike") formatted = `~${selected}~`;
        else if (format === "code") formatted = `\`${selected}\``;

        this.state.replyText = val.substring(0, start) + formatted + val.substring(end);
        setTimeout(() => {
            input.focus();
            input.setSelectionRange(start + formatted.length, start + formatted.length);
        }, 50);
    }

    openKanbanView() {
        this.action.doAction("Oodnexa_whatspp_live_conversation.action_wa_conversation_list", {
            clearBreadcrumbs: true,
        });
    }

    openPartner() {
        if (!this.state.selectedConv || !this.state.selectedConv.partner_id) return;
        const partnerId = this.state.selectedConv.partner_id[0];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: this.state.selectedConv.partner_id[1] || "Contact",
            res_model: "res.partner",
            res_id: partnerId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    createQuotation() {
        if (!this.state.selectedConv || !this.state.selectedConv.partner_id) {
            this.notification.add("Please link a customer first to create a quotation.", { type: "warning" });
            return;
        }
        const partnerId = this.state.selectedConv.partner_id[0];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "New Quotation",
            res_model: "sale.order",
            views: [[false, "form"]],
            target: "current",
            context: { default_partner_id: partnerId },
        });
    }

    openSalesOrders() {
        if (!this.state.selectedConv || !this.state.selectedConv.partner_id) {
            this.notification.add("Please link a customer first to view sales orders.", { type: "warning" });
            return;
        }
        const partnerId = this.state.selectedConv.partner_id[0];
        const partnerName = this.state.selectedConv.partner_id[1] || "Customer";
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `Sales Orders - ${partnerName}`,
            res_model: "sale.order",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [["partner_id", "=", partnerId]],
            context: { default_partner_id: partnerId },
            target: "current",
        });
    }

    getSupportedAudioMime() {
        if (typeof MediaRecorder === "undefined") return "";
        if (MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")) return "audio/ogg;codecs=opus";
        if (MediaRecorder.isTypeSupported("audio/ogg")) return "audio/ogg";
        if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) return "audio/webm;codecs=opus";
        if (MediaRecorder.isTypeSupported("audio/webm")) return "audio/webm";
        if (MediaRecorder.isTypeSupported("audio/mp4")) return "audio/mp4";
        return "";
    }

    playChime() {
        try {
            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.type = "sine";
            osc.frequency.setValueAtTime(587.33, audioCtx.currentTime);
            osc.frequency.setValueAtTime(880, audioCtx.currentTime + 0.1);
            gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.35);
            osc.start(audioCtx.currentTime);
            osc.stop(audioCtx.currentTime + 0.35);
        } catch {}
    }

    async startVoiceRecording() {
        if (!this.state.selectedConvId) {
            this.notification.add("Please select a conversation first.", { type: "warning" });
            return;
        }

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            this.notification.add("Microphone recording is not supported by your browser or requires HTTPS.", { type: "danger" });
            return;
        }

        try {
            this.recordingStream = await navigator.mediaDevices.getUserMedia({
                audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
            });
        } catch (err) {
            this.notification.add("Microphone permission was denied. Please allow microphone access in your browser.", { type: "warning" });
            return;
        }

        const mime = this.getSupportedAudioMime();
        this.activeRecordingMime = mime || "audio/ogg";

        try {
            this.mediaRecorder = new MediaRecorder(this.recordingStream, mime ? { mimeType: mime } : undefined);
        } catch (recErr) {
            this.mediaRecorder = new MediaRecorder(this.recordingStream);
            this.activeRecordingMime = this.mediaRecorder.mimeType || "audio/ogg";
        }

        this.audioChunks = [];
        this.mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                this.audioChunks.push(event.data);
            }
        };

        this.recordingSeconds = 0;
        this.state.recordingTimer = "00:00";
        this.state.isRecording = true;

        clearInterval(this.recordingInterval);
        this.recordingInterval = setInterval(() => {
            this.recordingSeconds++;
            const mins = String(Math.floor(this.recordingSeconds / 60)).padStart(2, "0");
            const secs = String(this.recordingSeconds % 60).padStart(2, "0");
            this.state.recordingTimer = `${mins}:${secs}`;
        }, 1000);

        this.mediaRecorder.start(250);
    }

    cancelVoiceRecording() {
        clearInterval(this.recordingInterval);
        if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
            try { this.mediaRecorder.stop(); } catch {}
        }
        if (this.recordingStream) {
            this.recordingStream.getTracks().forEach((track) => track.stop());
            this.recordingStream = null;
        }
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.recordingSeconds = 0;
        this.state.isRecording = false;
        this.state.isSendingVoice = false;
        this.state.recordingTimer = "00:00";
    }

    async sendVoiceRecording() {
        if (!this.mediaRecorder || this.mediaRecorder.state === "inactive") return;
        const convId = this.state.selectedConvId;
        const durationSec = this.recordingSeconds;
        clearInterval(this.recordingInterval);

        this.state.isSendingVoice = true;

        this.mediaRecorder.onstop = async () => {
            if (this.recordingStream) {
                this.recordingStream.getTracks().forEach((track) => track.stop());
                this.recordingStream = null;
            }

            const blobMime = this.activeRecordingMime.split(";")[0] || "audio/ogg";
            const audioBlob = new Blob(this.audioChunks, { type: blobMime });

            const reader = new FileReader();
            reader.readAsDataURL(audioBlob);
            reader.onloadend = async () => {
                const base64Data = (reader.result || "").split(",")[1];
                if (!base64Data) {
                    this.notification.add("Failed to process recorded audio.", { type: "danger" });
                    this.cancelVoiceRecording();
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
                        this.notification.add(`Failed to send voice note: ${res.error}`, { type: "danger" });
                    } else {
                        this.playChime();
                        this.notification.add("Voice note sent successfully!", { type: "success" });
                        this.cancelVoiceRecording();
                        await this.selectConversation(convId);
                        await this.loadConversations();
                    }
                } catch (err) {
                    this.notification.add(`Error sending voice note: ${err.message || err}`, { type: "danger" });
                } finally {
                    this.cancelVoiceRecording();
                }
            };
        };

        this.mediaRecorder.stop();
    }

    async loadChatHistory(convId) {
        await this.selectConversation(convId || this.state.selectedConvId);
    }

    createConversation() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "New WhatsApp Conversation",
            res_model: "whatsapp.automation.conversation",
            views: [[false, "form"]],
            target: "current",
        });
    }

    editConversation() {
        if (!this.state.selectedConvId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: this.state.selectedConv?.contact_display_name || "Edit Conversation",
            res_model: "whatsapp.automation.conversation",
            res_id: this.state.selectedConvId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    backToConversations() {
        this.state.mobilePane = "list";
        this.state.showMobileDetails = false;
    }

    toggleMobileDetails() {
        this.state.showMobileDetails = !this.state.showMobileDetails;
    }

    openFullForm() {
        if (!this.state.selectedConvId) return;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: this.state.selectedConv?.contact_display_name || "Conversation",
            res_model: "whatsapp.automation.conversation",
            res_id: this.state.selectedConvId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

WaLiveConsole.template = "Oodnexa_whatspp_live_conversation.LiveConsole";

registry.category("actions").add("Oodnexa_whatspp_live_conversation.live_console", WaLiveConsole);
