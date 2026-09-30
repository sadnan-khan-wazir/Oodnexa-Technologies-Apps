import base64
import html
import logging
import os
import re
import struct
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from ..services.provider_adapter import normalize_phone

_logger = logging.getLogger(__name__)


def clean_phone_digits(phone):
    """Return only digits from phone number."""
    if not phone:
        return ''
    return re.sub(r'\D', '', phone)


def format_whatsapp_markdown(text):
    """Convert WhatsApp markdown (*bold*, _italic_, ~strike~, `code`) to safe HTML."""
    if not text:
        return ''
    escaped = html.escape(text)
    # Bold: *text* -> <strong>text</strong>
    escaped = re.sub(r'(?<!\w)\*([^\*\n]+)\*(?!\w)', r'<strong>\1</strong>', escaped)
    # Italic: _text_ -> <em>\1</em>
    escaped = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)', r'<em>\1</em>', escaped)
    # Strikethrough: ~text~ -> <del>\1</del>
    escaped = re.sub(r'(?<!\w)~([^~\n]+)~(?!\w)', r'<del>\1</del>', escaped)
    # Code block: ```text```
    escaped = re.sub(r'```([^`]+)```', r'<pre style="margin:4px 0;background:#f0f2f5;padding:4px 6px;border-radius:4px;font-family:monospace;font-size:12px;"><code>\1</code></pre>', escaped)
    # Inline code: `text` -> <code>text</code>
    escaped = re.sub(r'`([^`\n]+)`', r'<code style="background:#f0f2f5;padding:1px 4px;border-radius:3px;font-family:monospace;font-size:12px;">\1</code>', escaped)
    # Clickable URLs
    escaped = re.sub(r'(https?://[^\s<>"]+)', r'<a href="\1" target="_blank" rel="noopener noreferrer" style="color: #027eb5; text-decoration: underline;">\1</a>', escaped)
    return escaped.replace('\n', '<br/>')


class WhatsappAutomationConversation(models.Model):
    _name = 'whatsapp.automation.conversation'
    _description = 'WhatsApp Live Conversation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'last_message_at desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Title',
        compute='_compute_name',
        store=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        tracking=True,
        index=True,
    )
    phone = fields.Char(
        string='WhatsApp Number',
        required=True,
        index=True,
        tracking=True,
    )
    provider_id = fields.Many2one(
        'whatsapp.automation.provider',
        string='Provider',
        default=lambda self: self._default_provider(),
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Assigned Agent',
        default=lambda self: self.env.user,
        tracking=True,
    )
    state = fields.Selection(
        [
            ('active', 'Active'),
            ('archived', 'Archived'),
        ],
        default='active',
        tracking=True,
        index=True,
    )
    last_message_at = fields.Datetime(
        string='Last Message Time',
        default=fields.Datetime.now,
        index=True,
    )
    last_message_text = fields.Text(
        string='Last Message',
    )
    last_message_direction = fields.Selection(
        [
            ('incoming', 'Incoming'),
            ('outgoing', 'Outgoing'),
        ],
        default='incoming',
    )
    unread_count = fields.Integer(
        string='Unread',
        default=0,
    )
    message_ids = fields.One2many(
        'whatsapp.automation.message',
        'conversation_id',
        string='Messages',
    )
    message_count = fields.Integer(
        string='Total Messages',
        compute='_compute_message_count',
    )
    current_reply_text = fields.Text(
        string='Reply Content',
    )
    reply_attachment_ids = fields.Many2many(
        'ir.attachment',
        'wa_conversation_attachment_rel',
        'conversation_id',
        'attachment_id',
        string='Attachments',
        help='Attach PDF documents, invoices, or images to send to customer.',
    )
    chat_history_html = fields.Html(
        string='Chat Stream',
        compute='_compute_chat_history_html',
        sanitize=False,
    )
    contact_display_name = fields.Char(
        string='Contact Name',
        compute='_compute_contact_display_name',
        store=True,
    )
    contact_initials = fields.Char(
        string='Initials',
        compute='_compute_contact_display_name',
        store=True,
    )
    last_message_status = fields.Selection(
        [
            ('draft', 'Draft'),
            ('sent', 'Sent'),
            ('delivered', 'Delivered'),
            ('read', 'Read'),
            ('failed', 'Failed'),
        ],
        string='Delivery Status',
        compute='_compute_last_message_status',
        store=True,
    )
    # Related fields for WhatsApp Configuration card (Screenshot 4)
    provider_status = fields.Selection(
        related='provider_id.status',
        string='Provider Status',
        readonly=True,
    )
    provider_display_phone = fields.Char(
        related='provider_id.display_phone_number',
        string='Provider Phone',
        readonly=True,
    )
    provider_phone_number_id = fields.Char(
        related='provider_id.phone_number_id',
        string='Provider Phone Number ID',
        readonly=True,
    )
    provider_sandbox_mode = fields.Boolean(
        related='provider_id.sandbox_mode',
        string='Sandbox Mode',
        readonly=True,
    )
    provider_quality_rating = fields.Selection(
        related='provider_id.quality_rating',
        string='Provider Quality Rating',
        readonly=True,
    )
    provider_verified_name = fields.Char(
        related='provider_id.verified_name',
        string='Provider Business Name',
        readonly=True,
    )
    provider_account_mode_label = fields.Char(
        string='Account Mode',
        compute='_compute_provider_account_mode',
    )
    has_sales_orders = fields.Boolean(
        string='Has Sales Orders',
        compute='_compute_has_sales_orders',
        store=True,
    )

    @api.depends('partner_id')
    def _compute_has_sales_orders(self):
        so_model = self.env['sale.order']
        for rec in self:
            if rec.partner_id:
                rec.has_sales_orders = bool(so_model.search_count([('partner_id', '=', rec.partner_id.id)], limit=1))
            else:
                rec.has_sales_orders = False

    @api.depends('partner_id', 'partner_id.name', 'phone')
    def _compute_contact_display_name(self):
        for rec in self:
            name = rec.partner_id.name if rec.partner_id and rec.partner_id.name else rec.phone or _('Unknown')
            rec.contact_display_name = name
            parts = (name or '').strip().split()
            if len(parts) >= 2:
                rec.contact_initials = (parts[0][0] + parts[1][0]).upper()
            elif len(parts) == 1 and len(parts[0]) > 0:
                rec.contact_initials = parts[0][:2].upper()
            else:
                rec.contact_initials = 'WA'

    @api.depends('message_ids', 'message_ids.state')
    def _compute_last_message_status(self):
        for rec in self:
            last_msg = rec.message_ids.sorted('create_date')[-1:]
            if last_msg:
                rec.last_message_status = last_msg.state if last_msg.state in ('sent', 'delivered', 'read', 'failed') else 'delivered'
            else:
                rec.last_message_status = 'delivered'

    @api.depends('provider_id', 'provider_id.sandbox_mode')
    def _compute_provider_account_mode(self):
        for rec in self:
            if rec.provider_id:
                rec.provider_account_mode_label = _('Sandbox / Test') if rec.provider_id.sandbox_mode else _('Production')
            else:
                rec.provider_account_mode_label = _('Not Set')

    @api.model
    def _default_provider(self):
        return self.env['whatsapp.automation.provider'].search([
            ('default_provider', '=', True),
            '|', ('company_id', '=', self.env.company.id), ('company_id', '=', False),
        ], limit=1) or self.env['whatsapp.automation.provider'].search([
            ('active', '=', True),
        ], limit=1)

    @api.onchange('partner_id')
    def _onchange_partner_id_phone(self):
        if self.partner_id and not self.phone:
            self.phone = self.partner_id.mobile or self.partner_id.phone or False

    @api.depends('partner_id.name', 'phone')
    def _compute_name(self):
        for rec in self:
            if rec.partner_id and rec.partner_id.name:
                rec.name = f"{rec.partner_id.name} ({rec.phone or ''})"
            else:
                rec.name = rec.phone or _('New Conversation')

    total_customer_conversations = fields.Integer(
        string='Total Conversations',
        compute='_compute_total_customer_conversations',
    )

    @api.depends('message_ids')
    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    @api.depends('partner_id', 'phone')
    def _compute_total_customer_conversations(self):
        for conv in self:
            if conv.partner_id:
                conv.total_customer_conversations = self.search_count([('partner_id', '=', conv.partner_id.id)])
            elif conv.phone:
                conv.total_customer_conversations = self.search_count([('phone', '=', conv.phone)])
            else:
                conv.total_customer_conversations = 1

    @api.depends('message_ids', 'message_ids.state', 'message_ids.rendered_body', 'message_ids.direction', 'message_ids.attachment_ids', 'message_ids.is_edited', 'message_ids.is_deleted')
    def _compute_chat_history_html(self):
        for conv in self:
            messages = conv.message_ids.sorted('create_date')
            if not messages:
                conv.chat_history_html = (
                    f'<div id="wa_chat_history_box" data-conversation-id="{conv.id}" data-message-count="0" '
                    'style="background-color: #efeae2; min-height: 380px; display: flex; align-items: center; justify-content: center; border-radius: 8px; color: #888;">'
                    '<div style="text-align: center;">'
                    '<i class="fa fa-comments fa-3x" style="color: #25d366; margin-bottom: 10px;"></i>'
                    '<p style="margin: 0; font-size: 14px; font-weight: 500; color: #54656f;">No messages in this conversation yet.</p>'
                    '<p style="margin-top: 4px; font-size: 12px; color: #8696a0;">Type a reply or attach a document below to begin messaging!</p>'
                    '</div>'
                    '</div>'
                )
                continue

            html_parts = [
                f'<div id="wa_chat_history_box" data-conversation-id="{conv.id}" data-message-count="{len(messages)}" '
                'style="background-color: #efeae2; min-height: 400px; max-height: 560px; overflow-y: auto; padding: 18px; border-radius: 8px; font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif;">'
            ]

            last_date_str = None

            for msg in messages:
                msg_date = msg.create_date.strftime('%Y-%m-%d') if msg.create_date else ''
                msg_time = msg.create_date.strftime('%H:%M') if msg.create_date else ''

                # Date separator
                if msg_date != last_date_str:
                    last_date_str = msg_date
                    display_date = msg.create_date.strftime('%B %d, %Y') if msg.create_date else ''
                    html_parts.append(f"""
                        <div style="text-align: center; margin: 12px 0;">
                            <span style="background-color: #ffffff; color: #54656f; font-size: 11px; padding: 4px 12px; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.1); font-weight: 500;">
                                {display_date}
                            </span>
                        </div>
                    """)

                body_formatted = format_whatsapp_markdown(msg.rendered_body or '')
                is_outgoing = (msg.direction == 'outgoing')
                is_deleted = getattr(msg, 'is_deleted', False) or (msg.state == 'cancelled')
                is_edited = getattr(msg, 'is_edited', False)

                # Deleted message bubble
                if is_deleted:
                    justify = "flex-end" if is_outgoing else "flex-start"
                    html_parts.append(f"""
                        <div style="display: flex; justify-content: {justify}; margin-bottom: 8px;">
                            <div style="background-color: #f0f2f5; color: #8696a0; padding: 7px 12px; border-radius: 8px; font-size: 12px; font-style: italic; border: 1px dashed #d1d7db; display: flex; align-items: center; gap: 6px;">
                                <i class="fa fa-ban" style="color: #8696a0;"></i>
                                <span>This message was deleted</span>
                                <span style="font-size: 9.5px; color: #aebac1; margin-left: 6px; font-style: normal;">{msg_time}</span>
                            </div>
                        </div>
                    """)
                    continue

                # Attachment cards inside bubble
                att_html_list = []
                for att in msg.attachment_ids:
                    mimetype = (att.mimetype or '').lower()
                    is_img = mimetype.startswith('image/')
                    is_audio = mimetype.startswith('audio/')
                    is_video = mimetype.startswith('video/')
                    att_name = html.escape(att.name or 'file')

                    if is_img:
                        att_html_list.append(f"""
                            <div style="margin-bottom: 6px;">
                                <div class="wa-attachment-preview" data-att-id="{att.id}" data-name="{att_name}" data-mimetype="{mimetype}" style="cursor: pointer;" title="Click to preview image in popup">
                                    <img src="/web/image/{att.id}" style="max-width: 260px; max-height: 200px; border-radius: 6px; display: block; object-fit: cover; box-shadow: 0 1px 2px rgba(0,0,0,0.1);"/>
                                </div>
                                <div style="display: flex; gap: 10px; margin-top: 4px; font-size: 11px;">
                                    <a href="javascript:void(0)" class="wa-attachment-preview text-primary text-decoration-none fw-semibold" data-att-id="{att.id}" data-name="{att_name}" data-mimetype="{mimetype}">
                                        <i class="fa fa-search-plus me-1"></i>Preview
                                    </a>
                                    <a href="/web/content/{att.id}?download=true" class="text-secondary text-decoration-none fw-semibold ms-auto" download="{att_name}" title="Download to offline system">
                                        <i class="fa fa-download me-1"></i>Download
                                    </a>
                                </div>
                            </div>
                        """)
                    elif is_video:
                        att_html_list.append(f"""
                            <div style="margin-bottom: 6px;">
                                <div class="wa-attachment-preview" data-att-id="{att.id}" data-name="{att_name}" data-mimetype="{mimetype}" style="position: relative; max-width: 260px; border-radius: 6px; overflow: hidden; background: #000; cursor: pointer;" title="Click to play video in popup preview">
                                    <video preload="metadata" src="/web/content/{att.id}" style="max-width: 260px; max-height: 180px; display: block; pointer-events: none;"></video>
                                    <div style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center; background: rgba(0,0,0,0.35);">
                                        <span style="background: rgba(255,255,255,0.9); border-radius: 50%; width: 44px; height: 44px; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.3);">
                                            <i class="fa fa-play text-dark" style="font-size: 18px; margin-left: 3px;"></i>
                                        </span>
                                    </div>
                                </div>
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px; font-size: 11px;">
                                    <a href="javascript:void(0)" class="wa-attachment-preview text-primary text-decoration-none fw-semibold" data-att-id="{att.id}" data-name="{att_name}" data-mimetype="{mimetype}">
                                        <i class="fa fa-expand me-1"></i>Preview Video
                                    </a>
                                    <a href="/web/content/{att.id}?download=true" class="text-secondary text-decoration-none fw-semibold" download="{att_name}" title="Download video to offline system">
                                        <i class="fa fa-download me-1"></i>Download
                                    </a>
                                </div>
                            </div>
                        """)
                    elif is_audio:
                        att_html_list.append(f"""
                            <div style="background-color: rgba(0,0,0,0.03); border: 1px solid rgba(0,0,0,0.08); border-radius: 8px; padding: 6px 10px; margin-bottom: 6px;">
                                <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                                    <i class="fa fa-microphone" style="color: #008069; font-size: 15px;"></i>
                                    <span style="font-weight: 600; font-size: 11.5px; color: #111b21;">Voice Message</span>
                                    <a href="/web/content/{att.id}?download=true" class="text-secondary text-decoration-none ms-auto" download="{att_name}" style="font-size: 11px;" title="Download audio">
                                        <i class="fa fa-download"></i>
                                    </a>
                                </div>
                                <audio controls preload="metadata" src="/web/content/{att.id}" style="width: 100%; max-width: 250px; height: 32px; display: block;"></audio>
                            </div>
                        """)
                    else:
                        att_html_list.append(f"""
                            <div style="background-color: rgba(0,0,0,0.06); padding: 8px 10px; border-radius: 6px; margin-bottom: 6px;">
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <i class="fa fa-file-text text-danger" style="font-size: 22px;"></i>
                                    <div style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                        <div style="font-weight: 600; font-size: 12px; color: #111b21;">{att_name}</div>
                                        <div style="font-size: 10px; color: #667781;">{att.file_size or ''} bytes</div>
                                    </div>
                                </div>
                                <div style="display: flex; gap: 12px; margin-top: 6px; padding-top: 4px; border-top: 1px solid rgba(0,0,0,0.06); font-size: 11px;">
                                    <a href="javascript:void(0)" class="wa-attachment-preview text-primary text-decoration-none fw-semibold" data-att-id="{att.id}" data-name="{att_name}" data-mimetype="{mimetype}">
                                        <i class="fa fa-eye me-1"></i>Preview
                                    </a>
                                    <a href="/web/content/{att.id}?download=true" class="text-secondary text-decoration-none fw-semibold ms-auto" download="{att_name}" title="Download to offline system">
                                        <i class="fa fa-download me-1"></i>Download
                                    </a>
                                </div>
                            </div>
                        """)

                att_html = "".join(att_html_list)
                edited_label = '<span style="font-size: 9.5px; color: #667781; font-style: italic; margin-left: 4px;">(edited)</span>' if is_edited else ''

                if is_outgoing:
                    # Status checkmark icon
                    if msg.state == 'read':
                        status_icon = '<span style="color: #53bdeb; margin-left: 4px; font-weight: bold;" title="Read by customer">&#10003;&#10003;</span>'
                    elif msg.state == 'delivered':
                        status_icon = '<span style="color: #8696a0; margin-left: 4px; font-weight: bold;" title="Delivered to phone">&#10003;&#10003;</span>'
                    elif msg.state == 'sent':
                        status_icon = '<span style="color: #8696a0; margin-left: 4px;" title="Sent to WhatsApp">&#10003;</span>'
                    elif msg.state == 'failed':
                        err_title = html.escape(msg.error_message or 'Delivery Failed by WhatsApp')
                        status_icon = f'<span style="color: #ea0038; margin-left: 4px; cursor: help;" title="{err_title}">&#9888;</span>'
                    else:
                        status_icon = ''

                    html_parts.append(f"""
                        <div style="display: flex; justify-content: flex-end; margin-bottom: 8px;">
                            <div style="background-color: #d9fdd3; color: #111b21; padding: 8px 12px; border-radius: 8px 8px 0px 8px; max-width: 75%; box-shadow: 0 1px 1px rgba(0,0,0,0.13); font-size: 13.5px; line-height: 1.45; word-wrap: break-word;">
                                {att_html}
                                {f'<div>{body_formatted}</div>' if body_formatted else ''}
                                <div style="display: flex; justify-content: flex-end; align-items: center; font-size: 10.5px; color: #667781; margin-top: 4px;">
                                    <span>{msg_time}</span>
                                    {edited_label}
                                    {status_icon}
                                </div>
                            </div>
                        </div>
                    """)
                else:
                    # Incoming message
                    sender_title = conv.partner_id.name if conv.partner_id else conv.phone
                    html_parts.append(f"""
                        <div style="display: flex; justify-content: flex-start; margin-bottom: 8px;">
                            <div style="background-color: #ffffff; color: #111b21; padding: 8px 12px; border-radius: 8px 8px 8px 0px; max-width: 75%; box-shadow: 0 1px 1px rgba(0,0,0,0.13); font-size: 13.5px; line-height: 1.45; word-wrap: break-word;">
                                <div style="font-weight: 600; color: #008069; font-size: 11.5px; margin-bottom: 3px;">{html.escape(sender_title or '')}</div>
                                {att_html}
                                {f'<div>{body_formatted}</div>' if body_formatted else ''}
                                <div style="display: flex; justify-content: flex-end; align-items: center; font-size: 10.5px; color: #667781; margin-top: 4px;">
                                    <span>{msg_time}</span>
                                    {edited_label}
                                </div>
                            </div>
                        </div>
                    """)

            html_parts.append('</div>')
            conv.chat_history_html = "".join(html_parts).strip()

    def action_send_reply(self):
        """Send live text or document message to customer via Meta Cloud API."""
        self.ensure_one()
        reply_content = (self.current_reply_text or '').strip()
        attachments = self.reply_attachment_ids

        if not reply_content and not attachments:
            raise UserError(_('Please enter a message or attach a file to send.'))

        provider = self.provider_id or self._default_provider()
        if not provider:
            raise UserError(_('No active WhatsApp provider found. Please configure Meta Cloud API in Settings.'))

        adapter_service = self.env['whatsapp.automation.provider.adapter']
        sent_msg_ids = []

        # Whitelist of MIME types strictly supported by Meta WhatsApp Cloud API
        META_SUPPORTED_MIME_TYPES = {
            'audio/aac', 'audio/mp4', 'audio/mpeg', 'audio/amr', 'audio/ogg', 'audio/opus',
            'application/pdf', 'text/plain',
            'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'image/jpeg', 'image/png', 'image/webp',
            'video/mp4', 'video/3gpp',
        }

        # 1. Send attachments if any
        if attachments:
            import mimetypes
            for att in attachments:
                mtype = (att.mimetype or '').lower().split(';')[0].strip()
                ext = (att.name or '').rsplit('.', 1)[-1].lower() if '.' in (att.name or '') else ''
                if not mtype or mtype in ('application/octet-stream', 'binary/octet-stream'):
                    guessed, _enc = mimetypes.guess_type(att.name or '')
                    if guessed:
                        mtype = guessed.lower().split(';')[0].strip()
                        att.sudo().write({'mimetype': mtype})
                if mtype not in META_SUPPORTED_MIME_TYPES:
                    if mtype.startswith('video/') or ext in ('mkv', 'avi', 'mov', 'wmv', 'flv', 'webm'):
                        raise UserError(_(
                            "WhatsApp does not support the video format of '%(name)s' (%(mime)s).\n\n"
                            "Meta WhatsApp Cloud API only supports MP4 (.mp4) and 3GP (.3gp) videos up to 16 MB.\n"
                            "Please convert or save this video as an MP4 file (H.264/AAC) before sending."
                        ) % {'name': att.name, 'mime': mtype or ext})
                    elif mtype.startswith('audio/'):
                        raise UserError(_(
                            "WhatsApp does not support the audio format of '%(name)s' (%(mime)s).\n\n"
                            "Supported audio formats are MP3 (audio/mpeg), AAC, M4A, AMR, and OGG/OPUS."
                        ) % {'name': att.name, 'mime': mtype or ext})
                    else:
                        raise UserError(_(
                            "File '%(name)s' (%(mime)s) is not a supported WhatsApp attachment.\n\n"
                            "Supported formats by WhatsApp:\n"
                            "• Documents: PDF, Word (DOC/DOCX), Excel (XLS/XLSX), PowerPoint (PPT/PPTX), TXT\n"
                            "• Videos: MP4, 3GP (max 16 MB)\n"
                            "• Images: JPG, PNG, WEBP (max 5 MB)\n"
                            "• Audio: MP3, AAC, M4A, AMR, OGG (max 16 MB)"
                        ) % {'name': att.name, 'mime': mtype or ext})

            for idx, att in enumerate(attachments):
                caption = reply_content if idx == 0 else None
                file_content = att.raw
                if not file_content and att.datas:
                    try:
                        file_content = base64.b64decode(att.datas)
                    except Exception:
                        file_content = att.datas.encode('utf-8') if isinstance(att.datas, str) else att.datas

                if not file_content:
                    continue

                mtype = (att.mimetype or 'application/pdf').lower().split(';')[0].strip()
                res = adapter_service.send_document(
                    provider,
                    phone=self.phone,
                    file_content=file_content,
                    filename=att.name,
                    caption=caption,
                    mime_type=mtype,
                )
                if not res.get('success'):
                    error_details = res.get('error', _('Unknown error sending attachment.'))
                    raise UserError(_('Failed to send attachment %s:\n%s') % (att.name, error_details))

                msg = self.env['whatsapp.automation.message'].create({
                    'company_id': self.company_id.id,
                    'provider_id': provider.id,
                    'conversation_id': self.id,
                    'partner_id': self.partner_id.id if self.partner_id else False,
                    'phone': self.phone,
                    'state': 'sent',
                    'message_type': 'freeform',
                    'direction': 'outgoing',
                    'rendered_body': f"[{att.name}] {caption or ''}".strip(),
                    'attachment_ids': [(4, att.id)],
                    'sent_at': fields.Datetime.now(),
                    'provider_message_id': res.get('provider_message_id', ''),
                })
                att.write({
                    'public': True,
                    'res_model': 'whatsapp.automation.message',
                    'res_id': msg.id,
                })
                msg._log_event('sent', f'Direct attachment reply sent to {self.phone}')
                sent_msg_ids.append(msg.id)

            self.write({
                'last_message_at': fields.Datetime.now(),
                'last_message_text': f"[{attachments[0].name}] {reply_content}".strip(),
                'last_message_direction': 'outgoing',
                'current_reply_text': False,
                'reply_attachment_ids': [(5, 0, 0)],
            })

        elif reply_content:
            # Send pure text message
            res = adapter_service.send_message(
                provider,
                phone=self.phone,
                body=reply_content,
            )
            if not res.get('success'):
                error_details = res.get('error', _('Unknown error from WhatsApp provider.'))
                raise UserError(_('Failed to send WhatsApp message:\n%s') % error_details)

            msg = self.env['whatsapp.automation.message'].create({
                'company_id': self.company_id.id,
                'provider_id': provider.id,
                'conversation_id': self.id,
                'partner_id': self.partner_id.id if self.partner_id else False,
                'phone': self.phone,
                'state': 'sent',
                'message_type': 'freeform',
                'direction': 'outgoing',
                'rendered_body': reply_content,
                'sent_at': fields.Datetime.now(),
                'provider_message_id': res.get('provider_message_id', ''),
            })
            msg._log_event('sent', f'Direct reply sent to {self.phone}')
            sent_msg_ids.append(msg.id)

            self.write({
                'last_message_at': fields.Datetime.now(),
                'last_message_text': reply_content,
                'last_message_direction': 'outgoing',
                'current_reply_text': False,
            })

        # Post to Chatter on customer record if linked
        if self.partner_id and reply_content:
            self.partner_id.message_post(
                body=f'<strong>Outgoing WhatsApp to {self.phone}:</strong><br/><p>{html.escape(reply_content)}</p>',
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

        # Broadcast bus notification for outgoing message (excluding current user since current user's view reloads natively)
        try:
            internal_users = self.env['res.users'].sudo().search([
                ('share', '=', False),
                ('id', '!=', self.env.uid),
            ])
            if internal_users:
                internal_users._bus_send(
                    'whatsapp.conversation/new_message',
                    {
                        'conversation_id': self.id,
                        'message_id': sent_msg_ids[0] if sent_msg_ids else False,
                        'sender_name': self.env.user.name or 'Staff',
                        'phone': self.phone,
                        'body': reply_content or 'Attachment',
                        'direction': 'outgoing',
                    }
                )
        except Exception as bus_err:
            _logger.warning('Failed to broadcast outgoing WhatsApp bus notification: %s', bus_err)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('WhatsApp Sent'),
                'message': _('Message successfully sent to %s.', self.phone),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    @api.model
    def _remux_webm_opus_to_ogg(self, webm_bytes):
        """Pure-Python remuxer from WebM/Matroska Opus (as produced by Chrome/Chromium MediaRecorder)
        to standard OGG Opus format required by Meta WhatsApp Cloud API.
        Zero external dependencies, fast and lossless.
        """
        if not webm_bytes or not webm_bytes.startswith(b'\x1aE\xdf\xa3'):
            return webm_bytes

        CRC_TABLE = []
        for i in range(256):
            r = i << 24
            for _ in range(8):
                if r & 0x80000000:
                    r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
                else:
                    r = (r << 1) & 0xFFFFFFFF
            CRC_TABLE.append(r)

        def ogg_crc(data):
            crc = 0
            for b in data:
                crc = ((crc << 8) & 0xFFFFFFFF) ^ CRC_TABLE[((crc >> 24) & 0xFF) ^ b]
            return crc

        def make_ogg_page(header_type, granule_pos, serial_no, seq_no, packets):
            seg_table = bytearray()
            payload = bytearray()
            for pkt in packets:
                length = len(pkt)
                while length >= 255:
                    seg_table.append(255)
                    length -= 255
                seg_table.append(length)
                payload.extend(pkt)

            header = bytearray(struct.pack(
                '<4sBBqIIIB',
                b'OggS',
                0,
                header_type,
                granule_pos,
                serial_no,
                seq_no,
                0,
                len(seg_table)
            ))
            header.extend(seg_table)
            page_data = header + payload
            crc = ogg_crc(page_data)
            struct.pack_into('<I', page_data, 22, crc)
            return bytes(page_data)

        def read_vint(data, pos, is_id=False):
            if pos >= len(data):
                return None, 0
            b = data[pos]
            if b == 0:
                return None, 0
            mask = 0x80
            length = 1
            while not (b & mask):
                mask >>= 1
                length += 1
                if length > 8:
                    return None, 0
            if pos + length > len(data):
                return None, 0
            val = 0
            for i in range(length):
                val = (val << 8) | data[pos + i]
            if not is_id:
                val &= ~(1 << (7 * length))
            return val, length

        opus_head = None
        if b'OpusHead' in webm_bytes:
            idx = webm_bytes.index(b'OpusHead')
            opus_head = webm_bytes[idx:idx + 19]
        if not opus_head or len(opus_head) < 19:
            # Standard 19-byte OpusHead: magic, version 1, channels 1, preskip 312, 48000Hz, gain 0, mapping 0
            opus_head = struct.pack('<8sBBHIhB', b'OpusHead', 1, 1, 312, 48000, 0, 0)

        vendor = b'Odoo WhatsApp'
        opus_tags = struct.pack('<8sI', b'OpusTags', len(vendor)) + vendor + struct.pack('<I', 0)

        serial = 0x57415453  # 'WATS'
        pages = [
            make_ogg_page(0x02, 0, serial, 0, [opus_head]),
            make_ogg_page(0x00, 0, serial, 1, [opus_tags]),
        ]

        pos = 0
        audio_packets = []
        container_ids = {0x1A45DFA3, 0x18538067, 0x1F43B675, 0x1654AE6B, 0xAE, 0xA0}
        while pos < len(webm_bytes):
            el_id, id_len = read_vint(webm_bytes, pos, is_id=True)
            if el_id is None or id_len == 0:
                pos += 1
                continue
            el_size, size_len = read_vint(webm_bytes, pos + id_len, is_id=False)
            if el_size is None or size_len == 0:
                pos += 1
                continue
            hdr_len = id_len + size_len
            if el_id in (0xA3, 0xA1):  # SimpleBlock or Block
                block_data = webm_bytes[pos + hdr_len:pos + hdr_len + el_size]
                tnum, tlen = read_vint(block_data, 0, is_id=False)
                payload = block_data[tlen + 3:]
                if payload:
                    audio_packets.append(payload)
                pos += hdr_len + el_size
            elif el_id in container_ids:
                pos += hdr_len
            else:
                pos += hdr_len + el_size

        if not audio_packets:
            return webm_bytes

        granule = 0
        seq = 2
        chunk_size = 10
        for i in range(0, len(audio_packets), chunk_size):
            batch = audio_packets[i:i + chunk_size]
            is_last = (i + chunk_size >= len(audio_packets))
            granule += len(batch) * 960
            hdr_type = 0x04 if is_last else 0x00
            pages.append(make_ogg_page(hdr_type, granule, serial, seq, batch))
            seq += 1

        return b''.join(pages)

    @api.model
    def _convert_to_ogg_ffmpeg(self, audio_bytes, input_format='mp4'):
        """Fallback to ffmpeg if installed on host to transcode arbitrary audio to OGG Opus."""
        import shutil
        import subprocess
        import tempfile
        ffmpeg_bin = shutil.which('ffmpeg')
        if not ffmpeg_bin:
            return None

        in_ext = input_format.replace('audio/', '').replace('video/', '').split(';')[0].strip() or 'tmp'
        try:
            with tempfile.NamedTemporaryFile(suffix=f'.{in_ext}', delete=False) as in_f:
                in_path = in_f.name
                in_f.write(audio_bytes)

            out_path = in_path + '.ogg'
            cmd = [
                ffmpeg_bin, '-y',
                '-i', in_path,
                '-c:a', 'libopus',
                '-b:a', '32k',
                '-ac', '1',
                out_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            if res.returncode == 0 and os.path.exists(out_path):
                with open(out_path, 'rb') as out_f:
                    ogg_data = out_f.read()
                try:
                    os.unlink(in_path)
                    os.unlink(out_path)
                except Exception:
                    pass
                return ogg_data
        except Exception as e:
            _logger.warning('WhatsApp: ffmpeg voice note conversion failed: %s', e)
        return None

    def action_send_voice_note_data(self, audio_base64, mime_type='audio/ogg', duration_sec=0):
        """Decode base64 voice note, convert/normalize to Meta-compliant OGG Opus,
        and transmit to WhatsApp via provider adapter.
        """
        self.ensure_one()
        provider = self.provider_id or self._default_provider()
        if not provider:
            return {'error': _('No active WhatsApp provider found. Please configure Meta Cloud API in Settings.')}

        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as e:
            return {'error': f"Failed to decode audio: {e}"}

        if not audio_bytes:
            return {'error': _('Voice note audio is empty.')}

        if len(audio_bytes) > 16 * 1024 * 1024:
            return {'error': _('Voice note exceeds WhatsApp 16 MB limit.')}

        clean_mime = (mime_type or 'audio/ogg').split(';')[0].strip().lower()

        # Step 1: Already valid Ogg Opus
        if audio_bytes.startswith(b'OggS'):
            clean_mime = 'audio/ogg'
        # Step 2: WebM / Matroska from Chrome/Edge -> remux to Ogg Opus in pure Python
        elif audio_bytes.startswith(b'\x1aE\xdf\xa3') or 'webm' in clean_mime or 'matroska' in clean_mime:
            try:
                ogg_bytes = self._remux_webm_opus_to_ogg(audio_bytes)
                if ogg_bytes and ogg_bytes.startswith(b'OggS'):
                    audio_bytes = ogg_bytes
                    clean_mime = 'audio/ogg'
                else:
                    ffmpeg_ogg = self._convert_to_ogg_ffmpeg(audio_bytes, input_format='webm')
                    if ffmpeg_ogg and ffmpeg_ogg.startswith(b'OggS'):
                        audio_bytes = ffmpeg_ogg
                        clean_mime = 'audio/ogg'
            except Exception as e:
                _logger.warning('Failed to remux WebM Opus to Ogg: %s', e)
        # Step 3: MP4 / other format -> try ffmpeg transcoding if available
        elif clean_mime in ('audio/mp4', 'audio/aac', 'audio/mpeg', 'audio/wav', 'audio/x-m4a'):
            ffmpeg_ogg = self._convert_to_ogg_ffmpeg(audio_bytes, input_format=clean_mime)
            if ffmpeg_ogg and ffmpeg_ogg.startswith(b'OggS'):
                audio_bytes = ffmpeg_ogg
                clean_mime = 'audio/ogg'

        ext = 'ogg' if 'ogg' in clean_mime else ('mp4' if 'mp4' in clean_mime else 'mp3')
        timestamp_str = fields.Datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"voice_note_{timestamp_str}.{ext}"

        adapter_service = self.env['whatsapp.automation.provider.adapter'].sudo()
        res = adapter_service.send_document(
            provider,
            phone=self.phone,
            file_content=audio_bytes,
            filename=filename,
            mime_type=clean_mime,
        )
        if not res.get('success'):
            error_details = res.get('error', _('Unknown error uploading voice note.'))
            return {'error': error_details}

        # Create attachment
        att = self.env['ir.attachment'].sudo().create({
            'name': filename,
            'raw': audio_bytes,
            'mimetype': clean_mime,
            'public': True,
            'res_model': 'whatsapp.automation.message',
        })

        dur_text = f" ({duration_sec}s)" if duration_sec else ""
        body_text = f"🎙️ Voice message{dur_text}"

        msg = self.env['whatsapp.automation.message'].sudo().create({
            'company_id': self.company_id.id,
            'provider_id': provider.id,
            'conversation_id': self.id,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'phone': self.phone,
            'state': 'sent',
            'message_type': 'freeform',
            'direction': 'outgoing',
            'rendered_body': body_text,
            'attachment_ids': [(4, att.id)],
            'sent_at': fields.Datetime.now(),
            'provider_message_id': res.get('provider_message_id', ''),
        })
        att.sudo().write({'res_id': msg.id})
        msg._log_event('sent', f'Direct voice message sent to {self.phone}')

        self.write({
            'last_message_at': fields.Datetime.now(),
            'last_message_text': body_text,
            'last_message_direction': 'outgoing',
        })

        # Post note to partner chatter if linked
        if self.partner_id:
            try:
                self.partner_id.message_post(
                    body=f'<strong>Outgoing Voice Note to {self.phone}:</strong> {body_text}',
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                    attachment_ids=[att.id],
                )
            except Exception as chatter_err:
                _logger.warning('Failed to post voice note to chatter: %s', chatter_err)

        return {'success': True, 'message_id': msg.id}

    def action_auto_mark_read(self):
        """Automatically mark conversation as read and notify Meta with read receipts."""
        for conv in self:
            if conv.unread_count > 0:
                conv.write({'unread_count': 0})
            conv._send_meta_read_receipts()

    def _send_meta_read_receipts(self):
        """Send read receipts to Meta for unread incoming messages."""
        for conv in self:
            incoming_unread = conv.message_ids.filtered(
                lambda m: m.direction == 'incoming' and m.state != 'read' and m.provider_message_id
            )
            if incoming_unread:
                incoming_unread.write({'state': 'read', 'read_at': fields.Datetime.now()})
                if conv.provider_id:
                    adapter_service = self.env['whatsapp.automation.provider.adapter']
                    for m in incoming_unread:
                        try:
                            adapter_service.mark_message_read(conv.provider_id, m.provider_message_id)
                        except Exception as err:
                            _logger.debug('Could not send Meta read receipt for %s: %s', m.provider_message_id, err)

    def action_mark_read(self):
        """Mark unread messages as read."""
        self.ensure_one()
        self.action_auto_mark_read()

    def action_view_messages(self):
        """Stat button: open list of all messages in this conversation."""
        self.ensure_one()
        return {
            'name': _('Messages (%s)') % (self.partner_id.name if self.partner_id else self.phone),
            'type': 'ir.actions.act_window',
            'res_model': 'whatsapp.automation.message',
            'view_mode': 'list,form',
            'domain': [('conversation_id', '=', self.id)],
            'context': {
                'default_conversation_id': self.id,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
                'default_phone': self.phone,
            },
        }

    def action_open_new_messages(self):
        """Stat button: mark unread messages as read."""
        self.ensure_one()
        self.action_auto_mark_read()
        return True

    def action_view_customer_conversations(self):
        """Stat button: view all conversations for this customer."""
        self.ensure_one()
        domain = [('partner_id', '=', self.partner_id.id)] if self.partner_id else [('phone', '=', self.phone)]
        return {
            'name': _('Customer Conversations (%s)') % (self.partner_id.name if self.partner_id else self.phone),
            'type': 'ir.actions.act_window',
            'res_model': 'whatsapp.automation.conversation',
            'view_mode': 'list,kanban,form',
            'domain': domain,
        }

    def action_open_partner(self):
        """Open the customer record."""
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_('No customer linked to this conversation.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.partner_id.name,
            'res_model': 'res.partner',
            'res_id': self.partner_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_create_sale_order(self):
        """Create a new quotation for this conversation's customer."""
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_('Please link a customer first to create a quotation.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Quotation'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_partner_id': self.partner_id.id,
            },
        }

    def action_view_sale_orders(self):
        """View existing sales orders for this conversation's customer."""
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_('Please link a customer first to view sales orders.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Orders - %s') % (self.partner_id.name,),
            'res_model': 'sale.order',
            'view_mode': 'list,kanban,form',
            'domain': [('partner_id', '=', self.partner_id.id)],
            'context': {
                'default_partner_id': self.partner_id.id,
            },
            'target': 'current',
        }

    def action_view_provider(self):
        """Open the provider form view."""
        self.ensure_one()
        if not self.provider_id:
            raise UserError(_('No WhatsApp Provider linked to this conversation.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('WhatsApp Provider'),
            'res_model': 'whatsapp.automation.provider',
            'res_id': self.provider_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_test_provider_connection(self):
        """Test connection of the provider."""
        self.ensure_one()
        if self.provider_id:
            return self.provider_id.action_test_connection()
        raise UserError(_('No WhatsApp Provider linked to this conversation.'))

    @api.model
    def get_or_create_conversation(self, phone, partner_id=None, provider_id=None):
        """Find or create a conversation record for a phone number."""
        if not phone:
            return False

        clean_phone = normalize_phone(phone) or phone
        digits = clean_phone_digits(clean_phone)

        # 1. Try finding conversation by exact phone, original phone, or matching last 8 digits
        conv = self.search([('phone', '=', clean_phone)], limit=1)
        if not conv and phone != clean_phone:
            conv = self.search([('phone', '=', phone)], limit=1)

        if not conv and len(digits) >= 8:
            conv = self.search([('phone', 'like', digits[-8:])], limit=1)

        if not conv and partner_id:
            conv = self.search([('partner_id', '=', partner_id)], limit=1)

        # 2. If no conversation exists in Odoo, create it!
        if not conv:
            default_prov = provider_id or (self._default_provider().id if self._default_provider() else False)
            conv = self.create({
                'phone': clean_phone,
                'partner_id': partner_id,
                'provider_id': default_prov,
                'state': 'active',
                'last_message_at': fields.Datetime.now(),
            })
            _logger.info('Created new WhatsApp live conversation %s (ID %s) for %s', conv.name, conv.id, clean_phone)
        else:
            # Re-activate conversation if it was archived so it shows in Active Conversations view
            vals_to_update = {}
            if conv.state == 'archived':
                vals_to_update['state'] = 'active'
            if partner_id and not conv.partner_id:
                vals_to_update['partner_id'] = partner_id
            if vals_to_update:
                conv.write(vals_to_update)

        return conv
