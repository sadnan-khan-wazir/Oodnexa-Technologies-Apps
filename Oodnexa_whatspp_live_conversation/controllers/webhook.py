"""
Webhook Controller
==================
Receives delivery status updates and incoming message notifications
from WhatsApp providers. Each provider type may send different payloads.
"""
import hashlib
import hmac
import html
import json
import logging
import re
import requests
from odoo import fields, http, _
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


class WhatsappWebhookController(http.Controller):

    @http.route(
        '/whatsapp_automation/webhook/<int:provider_id>',
        type='http', auth='none', methods=['GET', 'POST'],
        csrf=False, save_session=False,
    )
    def webhook_endpoint(self, provider_id, **kwargs):
        """
        Unified webhook endpoint for all providers.
        GET  → Webhook verification (Meta requires this)
        POST → Status updates / incoming messages
        """
        provider = request.env['whatsapp.automation.provider'].sudo().browse(provider_id)
        if not provider.exists() or not provider.active:
            return Response('Not Found', status=404)

        if request.httprequest.method == 'GET':
            return self._handle_verification(provider, kwargs)
        else:
            return self._handle_status_update(provider)

    def _handle_verification(self, provider, params):
        """Handle webhook verification (Meta Cloud API pattern)."""
        mode = params.get('hub.mode', '')
        token = params.get('hub.verify_token', '')
        challenge = params.get('hub.challenge', '')

        if mode == 'subscribe' and token and provider.webhook_secret:
            if hmac.compare_digest(token, provider.webhook_secret):
                _logger.info('Webhook verified for provider %s', provider.name)
                return Response(challenge, status=200, content_type='text/plain')

        _logger.warning('Webhook verification failed for provider %s', provider.name)
        return Response('Forbidden', status=403)

    def _handle_status_update(self, provider):
        """Handle incoming status updates and incoming customer messages."""
        try:
            raw_body = request.httprequest.get_data(as_text=True)
            if not raw_body:
                return Response('Empty', status=400)

            data = json.loads(raw_body)
        except (json.JSONDecodeError, TypeError):
            return Response('Invalid JSON', status=400)

        _logger.info('Webhook received for provider %s: %s', provider.name, raw_body[:500])

        try:
            # 1. Process status updates (sent, delivered, read, failed)
            statuses = self._extract_statuses(provider, data)
            for status_info in statuses:
                self._update_message_status(status_info)

            # 2. Process incoming customer messages
            incoming_messages = self._extract_incoming_messages(provider, data)
            for msg_info in incoming_messages:
                self._process_incoming_message(provider, msg_info)

        except Exception as e:
            _logger.error('Error processing webhook: %s', e, exc_info=True)

        return Response('OK', status=200)

    def _extract_statuses(self, provider, data):
        """Extract status updates from provider-specific webhook payload."""
        statuses = []

        if provider.provider_type == 'meta_cloud':
            # Extract change values (supports both standard 'entry[].changes[].value' and test modal 'value')
            values = []
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    if 'value' in change:
                        values.append(change['value'])
            if 'value' in data and not values:
                values.append(data['value'])

            for value in values:
                for status in value.get('statuses', []):
                    statuses.append({
                        'provider_message_id': status.get('id', ''),
                        'status': status.get('status', ''),
                        'timestamp': status.get('timestamp', ''),
                        'recipient': status.get('recipient_id', ''),
                        'errors': status.get('errors', []),
                    })
        else:
            # Generic: expect a flat structure
            if 'provider_message_id' in data:
                statuses.append(data)

        return statuses

    def _download_meta_media(self, provider, media_id):
        """Download media binary and mime type from Meta WhatsApp Cloud API."""
        if not provider or not provider.access_token or not media_id:
            return None, None
        try:
            url_endpoint = f"https://graph.facebook.com/v21.0/{media_id}"
            headers = {'Authorization': f'Bearer {provider.access_token}'}
            resp = requests.get(url_endpoint, headers=headers, timeout=15)
            if resp.status_code != 200:
                _logger.warning('Failed to retrieve media URL for %s: %s', media_id, resp.text)
                return None, None
            media_info = resp.json()
            media_url = media_info.get('url')
            mime_type = media_info.get('mime_type')
            if not media_url:
                return None, None

            dl_resp = requests.get(
                media_url,
                headers={'Authorization': f'Bearer {provider.access_token}', 'User-Agent': 'curl/7.68.0'},
                timeout=30,
            )
            if dl_resp.status_code == 200:
                return dl_resp.content, mime_type
        except Exception as e:
            _logger.error('Error downloading media %s from Meta: %s', media_id, e)
        return None, None

    def _extract_incoming_messages(self, provider, data):
        """Extract incoming customer messages and media from Meta Cloud API payload."""
        incoming = []
        if provider.provider_type == 'meta_cloud':
            # Extract change values (supports both standard 'entry[].changes[].value' and test modal 'value')
            values = []
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    if 'value' in change:
                        values.append(change['value'])
            if 'value' in data and not values:
                values.append(data['value'])

            for value in values:
                contacts = {}
                for c in value.get('contacts', []):
                    wa_id = c.get('wa_id', '')
                    name = c.get('profile', {}).get('name', '')
                    if wa_id and name:
                        contacts[wa_id] = name
                        contacts['+' + wa_id.lstrip('+')] = name
                        contacts[re.sub(r'\D', '', wa_id)] = name
                for msg in value.get('messages', []):
                    from_number = msg.get('from', '')
                    msg_type = msg.get('type', 'text')
                    body = ''
                    media_id = None
                    filename = None
                    mime_type = None

                    if msg_type == 'text':
                        body = msg.get('text', {}).get('body', '')
                    elif msg_type == 'image':
                        img_info = msg.get('image', {})
                        caption = img_info.get('caption')
                        body = caption or '[Photo received]'
                        media_id = img_info.get('id')
                        mime_type = img_info.get('mime_type', 'image/jpeg')
                        filename = f"whatsapp_photo_{msg.get('id', '')[-8:]}.jpg"
                    elif msg_type == 'document':
                        doc_info = msg.get('document', {})
                        filename = doc_info.get('filename') or 'document.pdf'
                        caption = doc_info.get('caption')
                        body = caption or f"[{filename}]"
                        media_id = doc_info.get('id')
                        mime_type = doc_info.get('mime_type', 'application/pdf')
                    elif msg_type == 'audio':
                        audio_info = msg.get('audio', {})
                        body = '[Voice message received]'
                        media_id = audio_info.get('id')
                        mime_type = audio_info.get('mime_type', 'audio/ogg')
                        filename = f"voice_message_{msg.get('id', '')[-8:]}.ogg"
                    elif msg_type == 'video':
                        video_info = msg.get('video', {})
                        caption = video_info.get('caption')
                        body = caption or '[Video received]'
                        media_id = video_info.get('id')
                        mime_type = video_info.get('mime_type', 'video/mp4')
                        filename = f"whatsapp_video_{msg.get('id', '')[-8:]}.mp4"
                    elif msg_type == 'location':
                        body = '[Location received]'
                    elif msg_type == 'interactive':
                        body = (
                            msg.get('interactive', {}).get('button_reply', {}).get('title')
                            or msg.get('interactive', {}).get('list_reply', {}).get('title')
                            or '[Interactive response]'
                        )
                    elif msg_type == 'button':
                        body = msg.get('button', {}).get('text', '')
                    else:
                        body = f'[{msg_type} message received]'

                    incoming.append({
                        'provider_message_id': msg.get('id', ''),
                        'from_number': from_number,
                        'contact_name': contacts.get(from_number, ''),
                        'timestamp': msg.get('timestamp', ''),
                        'body': body,
                        'msg_type': msg_type,
                        'media_id': media_id,
                        'filename': filename,
                        'mime_type': mime_type,
                    })
        return incoming

    def _process_incoming_message(self, provider, msg_info):
        """Save incoming message, download media attachment, link conversation, and post to Chatter."""
        from_number = msg_info.get('from_number')
        provider_msg_id = msg_info.get('provider_message_id')
        body = msg_info.get('body', '')
        contact_name = msg_info.get('contact_name', '')
        media_id = msg_info.get('media_id')
        filename = msg_info.get('filename')
        mime_type = msg_info.get('mime_type')

        if not from_number or not provider_msg_id:
            return

        MessageModel = request.env['whatsapp.automation.message'].sudo()
        existing = MessageModel.search([('provider_message_id', '=', provider_msg_id)], limit=1)
        if existing:
            _logger.info('Duplicate incoming WhatsApp message ignored: %s', provider_msg_id)
            return

        # Clean digits and format phone
        clean_phone = '+' + from_number.lstrip('+')
        digits = re.sub(r'\D', '', from_number)
        PartnerModel = request.env['res.partner'].sudo()
        partner = False
        if len(digits) >= 8:
            search_domain = [('phone', 'like', digits[-8:])]
            if 'mobile' in PartnerModel._fields:
                search_domain = ['|', ('phone', 'like', digits[-8:]), ('mobile', 'like', digits[-8:])]
            partner = PartnerModel.search(search_domain, limit=1)

        # If not found, auto-create customer record so every WhatsApp user has an Odoo partner
        if not partner:
            try:
                partner_name = (contact_name or '').strip() or clean_phone
                vals = {
                    'name': partner_name,
                    'phone': clean_phone,
                }
                if 'mobile' in PartnerModel._fields:
                    vals['mobile'] = clean_phone
                partner = PartnerModel.create(vals)
                _logger.info('Auto-created partner %s (ID %s) for WhatsApp number %s', partner_name, partner.id, clean_phone)
            except Exception as e:
                _logger.warning('Failed to auto-create partner for WhatsApp contact %s: %s', clean_phone, e)

        # Find or create conversation (guaranteed to create if none exists)
        ConvModel = request.env['whatsapp.automation.conversation'].sudo()
        conv = ConvModel.get_or_create_conversation(
            phone=clean_phone,
            partner_id=partner.id if partner else False,
            provider_id=provider.id,
        )

        # Download media if incoming message contains image, document, audio or video
        att_ids = []
        if media_id and provider.provider_type == 'meta_cloud':
            raw_bytes, dl_mime = self._download_meta_media(provider, media_id)
            if raw_bytes:
                try:
                    att = request.env['ir.attachment'].sudo().create({
                        'name': filename or f"whatsapp_{msg_info.get('msg_type', 'file')}",
                        'raw': raw_bytes,
                        'mimetype': dl_mime or mime_type or 'application/octet-stream',
                        'res_model': 'whatsapp.automation.message',
                        'public': True,
                    })
                    att_ids.append(att.id)
                except Exception as e:
                    _logger.warning('Failed to save incoming attachment for %s: %s', media_id, e)

        # Create incoming message record
        company_id = provider.company_id.id if provider.company_id else request.env.company.id
        msg_vals = {
            'company_id': company_id,
            'provider_id': provider.id,
            'conversation_id': conv.id if conv else False,
            'partner_id': partner.id if partner else False,
            'phone': clean_phone,
            'direction': 'incoming',
            'state': 'delivered',
            'message_type': 'freeform',
            'rendered_body': body,
            'provider_message_id': provider_msg_id,
            'sent_at': fields.Datetime.now(),
            'delivered_at': fields.Datetime.now(),
        }
        if att_ids:
            msg_vals['attachment_ids'] = [(6, 0, att_ids)]

        msg = MessageModel.create(msg_vals)
        if att_ids:
            request.env['ir.attachment'].sudo().browse(att_ids).write({'res_id': msg.id})

        sender_label = html.escape(partner.name if partner else clean_phone)
        safe_body = html.escape(body)

        # Update conversation status
        if conv:
            conv.write({
                'last_message_at': fields.Datetime.now(),
                'last_message_text': body,
                'last_message_direction': 'incoming',
                'unread_count': conv.unread_count + 1,
            })
            conv.message_post(
                body=f'<strong>Incoming WhatsApp from {sender_label}:</strong><br/><p>{safe_body}</p>',
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )

        # Post to Customer Chatter
        if partner:
            partner.message_post(
                body=f'<strong>Incoming WhatsApp:</strong><br/><p>{safe_body}</p>',
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
            # Post to open Quotation
            open_so = request.env['sale.order'].sudo().search([
                ('partner_id', '=', partner.id),
                ('state', 'in', ['draft', 'sent']),
            ], limit=1, order='create_date desc')
            if open_so:
                open_so.message_post(
                    body=f'<strong>Customer WhatsApp Reply:</strong><br/><p>{safe_body}</p>',
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )
            # Post to open Tailor Order if model exists
            if 'tailor.order' in request.env:
                open_tailor = request.env['tailor.order'].sudo().search([
                    ('partner_id', '=', partner.id),
                    ('state', 'not in', ['delivered', 'cancel']),
                ], limit=1, order='create_date desc')
                if open_tailor:
                    open_tailor.message_post(
                        body=f'<strong>Customer WhatsApp Reply:</strong><br/><p>{safe_body}</p>',
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',
                    )

        # Broadcast real-time bus notification so UI updates immediately without page refresh
        try:
            internal_users = request.env['res.users'].sudo().search([('share', '=', False)])
            internal_users._bus_send(
                'whatsapp.conversation/new_message',
                {
                    'conversation_id': conv.id if conv else False,
                    'message_id': msg.id,
                    'sender_name': partner.name if partner else clean_phone,
                    'phone': clean_phone,
                    'body': body,
                }
            )
        except Exception as bus_err:
            _logger.warning('Failed to broadcast incoming WhatsApp bus notification: %s', bus_err)

    @http.route('/whatsapp_automation/conversation/stream/<int:conv_id>', type='jsonrpc', auth='user')
    def get_conversation_stream(self, conv_id, **kwargs):
        """Return updated chat HTML and message metadata for real-time live refresh without page reload."""
        conv = request.env['whatsapp.automation.conversation'].browse(conv_id)
        if not conv.exists():
            return {'error': 'Not found'}
        # Auto-maintain read functionality: mark conversation as read upon viewing
        conv.action_auto_mark_read()
        conv.invalidate_recordset()
        return {
            'conversation_id': conv.id,
            'message_count': len(conv.message_ids),
            'last_message_at': str(conv.last_message_at or ''),
            'chat_history_html': conv.chat_history_html,
            'unread_count': conv.unread_count,
        }

    @http.route('/whatsapp_automation/conversation/send_voice_note', type='jsonrpc', auth='user')
    def send_voice_note(self, conv_id, audio_base64, mime_type='audio/ogg', duration_sec=0, **kwargs):
        """Send voice note recorded via browser microphone to customer on WhatsApp."""
        conv = request.env['whatsapp.automation.conversation'].sudo().browse(conv_id)
        if not conv.exists():
            return {'error': _('Conversation not found.')}
        if not audio_base64:
            return {'error': _('No audio data received.')}
        return conv.action_send_voice_note_data(audio_base64, mime_type=mime_type, duration_sec=duration_sec)


    def _update_message_status(self, status_info):
        """Update message state from webhook status."""
        provider_msg_id = status_info.get('provider_message_id')
        if not provider_msg_id:
            return

        message = request.env['whatsapp.automation.message'].sudo().search([
            ('provider_message_id', '=', provider_msg_id),
        ], limit=1)

        if not message:
            _logger.debug('No message found for provider_message_id: %s', provider_msg_id)
            return

        status = status_info.get('status', '').lower()
        now_str = message._fields['sent_at'].to_string(
            message._fields['sent_at'].from_string(
                message.env.cr.now()
            )
        ) if hasattr(message._fields['sent_at'], 'to_string') else str(message.env.cr.now())

        state_map = {
            'sent': 'sent',
            'delivered': 'delivered',
            'read': 'read',
            'failed': 'failed',
        }
        new_state = state_map.get(status)
        if not new_state:
            return

        vals = {'state': new_state}
        if new_state == 'delivered':
            vals['delivered_at'] = now_str
        elif new_state == 'read':
            vals['read_at'] = now_str
        elif new_state == 'failed':
            vals['failed_at'] = now_str
            errors = status_info.get('errors', [])
            if errors:
                err = errors[0]
                code = err.get('code')
                title = err.get('title', '')
                errmsg = err.get('message', '')
                details = err.get('error_data', {}).get('details', '')
                err_parts = []
                if code:
                    err_parts.append(f"(Code {code})")
                if title:
                    err_parts.append(title)
                if errmsg and errmsg != title:
                    err_parts.append(errmsg)
                if details:
                    err_parts.append(f"Details: {details}")
                vals['error_message'] = " - ".join(err_parts) or 'Delivery Failed by WhatsApp'

        message.write(vals)
        message._log_event('status_update', f'Status: {status}')

    @http.route(['/privacy', '/whatsapp/privacy', '/whatsapp_automation/privacy'], type='http', auth='public', csrf=False)
    def privacy_policy(self, **kwargs):
        """Public Privacy Policy page for Meta app verification."""
        privacy_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Privacy Policy - WhatsApp Notifications</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; max-width: 800px; margin: 40px auto; padding: 0 20px; color: #333; }
        h1 { color: #128C7E; border-bottom: 2px solid #25D366; padding-bottom: 10px; }
        h2 { color: #075E54; margin-top: 30px; }
    </style>
</head>
<body>
    <h1>Privacy Policy for WhatsApp Communication</h1>
    <p>Last updated: September 2026</p>
    <h2>1. Information We Collect</h2>
    <p>We only collect and process your phone number, name, and order details strictly necessary to deliver WhatsApp notifications, quotations, and order updates requested by you.</p>
    <h2>2. How We Use Information</h2>
    <p>Your WhatsApp contact information is used exclusively for sending direct transactional updates (such as order status, quotations, and tailoring updates). We do not share, sell, or rent your personal information to third parties.</p>
    <h2>3. Data Protection and Opt-Out</h2>
    <p>You may opt out of receiving WhatsApp messages from us at any time by replying 'STOP' or by contacting our customer support team.</p>
    <h2>4. Contact Us</h2>
    <p>If you have any questions about this Privacy Policy, please contact our support team.</p>
</body>
</html>"""
        return Response(privacy_html, status=200, content_type='text/html')
