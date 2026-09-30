"""
Provider Adapter Architecture
=============================
Abstract base adapter and concrete adapter registry for WhatsApp providers.
Each provider type gets its own adapter class implementing a common interface.
New providers can be added by extending the registry in separate modules.
"""
import json
import logging
import re
import requests
from odoo import api, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Phone number normalization
# --------------------------------------------------------------------------

_PHONE_STRIP_RE = re.compile(r'[^\d+]')


def normalize_phone(phone):
    """Strip non-digit characters (keep leading +) and validate length."""
    if not phone:
        return ''
    cleaned = _PHONE_STRIP_RE.sub('', phone)
    if not cleaned:
        return ''
    # Ensure leading +
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    # Minimal length check (country code + number)
    if len(cleaned) < 8 or len(cleaned) > 16:
        return ''
    return cleaned


# --------------------------------------------------------------------------
# Base Adapter (abstract-like)
# --------------------------------------------------------------------------

class BaseProviderAdapter:
    """Base class for all WhatsApp provider adapters."""

    def __init__(self, provider_record):
        self.provider = provider_record

    # -- Configuration --
    def validate_config(self):
        """Validate provider configuration. Return dict with 'success' bool."""
        if not self.provider.api_base_url:
            return {'success': False, 'error': 'API Base URL is required.'}
        return {'success': True}

    # -- Sending --
    def send_template_message(self, phone, template_name, language, variables=None, **kwargs):
        """
        Send a template-based message.
        Returns dict: {success, provider_message_id, error}
        """
        raise NotImplementedError('Subclass must implement send_template_message')

    def send_text_message(self, phone, body, **kwargs):
        """
        Send a freeform text message (not all providers support this).
        Returns dict: {success, provider_message_id, error}
        """
        raise NotImplementedError('Subclass must implement send_text_message')

    def upload_media(self, file_content, filename, mime_type='application/pdf'):
        """Upload media to provider. Returns dict: {success, media_id, error}"""
        raise NotImplementedError('Subclass must implement upload_media')

    def send_document_message(self, phone, file_content=None, filename=None, media_id=None,
                              caption=None, template_name=None, language=None, **kwargs):
        """Send a document/PDF message. Returns dict: {success, provider_message_id, error}"""
        raise NotImplementedError('Subclass must implement send_document_message')

    # -- Status & Receipts --
    def mark_message_read(self, message_id):
        """Send read receipt to provider."""
        return {'success': False}

    def fetch_delivery_status(self, provider_message_id):
        """
        Check delivery status of a message.
        Returns dict: {status, timestamp, error}
        """
        return {'status': 'unknown', 'error': 'Not implemented for this provider.'}

    # -- Helpers --
    def normalize_phone(self, phone):
        return normalize_phone(phone)

    def _extract_response_json(self, resp):
        """Safely extract JSON or error dict from Response even on HTTP error codes."""
        if resp is not None:
            try:
                return resp.json()
            except Exception:
                return {'error': {'message': resp.text or f"HTTP {resp.status_code}"}}
        return {}

    def parse_error(self, response_data):
        """Parse provider-specific error from response."""
        if isinstance(response_data, dict):
            error_obj = response_data.get('error')
            if isinstance(error_obj, dict):
                msg = error_obj.get('message', '')
                code = error_obj.get('code')
                details = error_obj.get('error_data', {}).get('details', '')
                parts = []
                if msg:
                    parts.append(msg)
                if code:
                    parts.append(f"(Code {code})")
                if details:
                    parts.append(f"- {details}")
                if parts:
                    return " ".join(parts)
                return str(error_obj)
            return str(response_data) if response_data else "Unknown error"
        return str(response_data)

    def _make_request(self, method, url, headers=None, json_data=None, timeout=30):
        """Safe HTTP request wrapper with timeout."""
        try:
            resp = requests.request(
                method, url,
                headers=headers or {},
                json=json_data,
                timeout=timeout,
            )
            return resp
        except requests.Timeout:
            _logger.error('Provider API timeout: %s %s', method, url)
            return None
        except requests.RequestException as e:
            _logger.error('Provider API error: %s', e)
            return None


# --------------------------------------------------------------------------
# Generic Adapter (works as a skeleton / test adapter)
# --------------------------------------------------------------------------

class GenericAdapter(BaseProviderAdapter):
    """Generic/test adapter — logs messages instead of sending."""

    def validate_config(self):
        _logger.info('GenericAdapter: validate_config for provider %s', self.provider.name)
        return {'success': True}

    def send_template_message(self, phone, template_name, language, variables=None, **kwargs):
        phone = self.normalize_phone(phone)
        if not phone:
            return {'success': False, 'error': 'Invalid phone number.'}

        _logger.info(
            'GenericAdapter: SEND template=%s phone=%s lang=%s vars=%s',
            template_name, phone, language, variables,
        )
        # In sandbox mode, simulate success
        if self.provider.sandbox_mode:
            return {
                'success': True,
                'provider_message_id': f'generic-sandbox-{phone}-{template_name}',
            }
        return {'success': False, 'error': 'Generic adapter cannot send real messages. Configure a real provider.'}

    def send_text_message(self, phone, body, **kwargs):
        phone = self.normalize_phone(phone)
        if not phone:
            return {'success': False, 'error': 'Invalid phone number.'}

        _logger.info('GenericAdapter: SEND text phone=%s body=%s', phone, body[:100])
        if self.provider.sandbox_mode:
            return {
                'success': True,
                'provider_message_id': f'generic-sandbox-text-{phone}',
            }
        return {'success': False, 'error': 'Generic adapter cannot send real messages.'}

    def upload_media(self, file_content, filename, mime_type='application/pdf'):
        return {'success': True, 'media_id': f'generic-media-{filename}'}

    def send_document_message(self, phone, file_content=None, filename=None, media_id=None,
                              caption=None, template_name=None, language=None, **kwargs):
        phone = self.normalize_phone(phone)
        if not phone:
            return {'success': False, 'error': 'Invalid phone number.'}
        _logger.info('GenericAdapter: SEND doc phone=%s filename=%s caption=%s', phone, filename, caption)
        return {'success': True, 'provider_message_id': f'generic-doc-{phone}'}


# --------------------------------------------------------------------------
# Meta Cloud API Adapter
# --------------------------------------------------------------------------

class MetaCloudAdapter(BaseProviderAdapter):
    """Adapter for Meta (Facebook) Cloud API for WhatsApp Business."""

    GRAPH_API_VERSION = 'v21.0'

    def validate_config(self):
        if not self.provider.access_token:
            return {'success': False, 'error': 'Access Token is required for Meta Cloud API.'}
        if not self.provider.phone_number_id:
            return {'success': False, 'error': 'Phone Number ID is required for Meta Cloud API.'}

        # Query phone number details and quality rating from Meta Graph API
        url = (
            f'https://graph.facebook.com/{self.GRAPH_API_VERSION}/{self.provider.phone_number_id}'
            '?fields=display_phone_number,verified_name,quality_rating,code_verification_status,messaging_limit_tier'
        )
        resp = self._make_request('GET', url, headers=self._headers())
        if resp is not None and resp.status_code == 200:
            data = resp.json() or {}
            latency_ms = int(resp.elapsed.total_seconds() * 1000) if hasattr(resp, 'elapsed') and resp.elapsed else 120
            tier_raw = data.get('messaging_limit_tier', 'TIER_250K')
            tier_label = tier_raw.replace('TIER_', '') + ' msgs/day' if 'TIER_' in str(tier_raw) else str(tier_raw)
            return {
                'success': True,
                'phone_number': data.get('display_phone_number', ''),
                'verified_name': data.get('verified_name', ''),
                'quality_rating': data.get('quality_rating', 'GREEN'),
                'messaging_tier': tier_label,
                'latency_ms': latency_ms,
            }
        error = self.parse_error(self._extract_response_json(resp))
        return {'success': False, 'error': error}

    def send_template_message(self, phone, template_name, language, variables=None, **kwargs):
        phone = self.normalize_phone(phone)
        if not phone:
            return {'success': False, 'error': 'Invalid phone number.'}

        # Strip leading + for Meta API (expects country code digits only)
        to_number = phone.lstrip('+')

        url = (
            f'https://graph.facebook.com/{self.GRAPH_API_VERSION}'
            f'/{self.provider.phone_number_id}/messages'
        )
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'template',
            'template': {
                'name': template_name,
                'language': {'code': language or 'en'},
            },
        }
        # Add template variables if provided
        if variables:
            components = []
            body_params = [{'type': 'text', 'text': str(v)} for v in variables]
            if body_params:
                components.append({'type': 'body', 'parameters': body_params})
            payload['template']['components'] = components

        if self.provider.sandbox_mode:
            _logger.info('MetaCloudAdapter SANDBOX: %s', json.dumps(payload, indent=2))
            return {
                'success': True,
                'provider_message_id': f'meta-sandbox-{to_number}',
            }

        resp = self._make_request('POST', url, headers=self._headers(), json_data=payload)
        if resp is not None and resp.status_code in (200, 201):
            data = resp.json()
            msg_id = data.get('messages', [{}])[0].get('id', '')
            return {'success': True, 'provider_message_id': msg_id}
        error = self.parse_error(self._extract_response_json(resp))
        return {'success': False, 'error': error}

    def send_text_message(self, phone, body, **kwargs):
        phone = self.normalize_phone(phone)
        if not phone:
            return {'success': False, 'error': 'Invalid phone number.'}

        to_number = phone.lstrip('+')
        url = (
            f'https://graph.facebook.com/{self.GRAPH_API_VERSION}'
            f'/{self.provider.phone_number_id}/messages'
        )
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'text',
            'text': {'body': body},
        }

        if self.provider.sandbox_mode:
            _logger.info('MetaCloudAdapter SANDBOX text: %s', json.dumps(payload, indent=2))
            return {
                'success': True,
                'provider_message_id': f'meta-sandbox-text-{to_number}',
            }

        resp = self._make_request('POST', url, headers=self._headers(), json_data=payload)
        if resp is not None and resp.status_code in (200, 201):
            data = resp.json()
            msg_id = data.get('messages', [{}])[0].get('id', '')
            return {'success': True, 'provider_message_id': msg_id}
        error = self.parse_error(self._extract_response_json(resp))
        return {'success': False, 'error': error}

    def upload_media(self, file_content, filename, mime_type='application/pdf'):
        """Upload media to Meta Graph API and return media ID."""
        if not file_content:
            return {'success': False, 'error': 'File content is empty.'}

        if self.provider.sandbox_mode:
            _logger.info('MetaCloudAdapter SANDBOX media upload: %s (%d bytes)', filename, len(file_content))
            return {'success': True, 'media_id': f'meta-sandbox-media-{len(file_content)}'}

        if not mime_type or mime_type == 'application/octet-stream':
            import mimetypes
            if filename:
                mime_type = mimetypes.guess_type(filename)[0]
        mime_type = (mime_type or 'application/pdf').lower()

        # Whitelist of MIME types strictly supported by Meta WhatsApp Cloud API
        META_SUPPORTED_MIME_TYPES = {
            # Audio (max 16 MB)
            'audio/aac', 'audio/mp4', 'audio/mpeg', 'audio/amr', 'audio/ogg', 'audio/opus',
            # Documents (max 100 MB)
            'application/pdf', 'text/plain',
            'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            # Images (max 5 MB)
            'image/jpeg', 'image/png', 'image/webp',
            # Video (max 16 MB)
            'video/mp4', 'video/3gpp',
        }

        if mime_type not in META_SUPPORTED_MIME_TYPES:
            ext = (filename or '').rsplit('.', 1)[-1].lower() if '.' in (filename or '') else ''
            if mime_type.startswith('video/') or ext in ('mkv', 'avi', 'mov', 'wmv', 'flv', 'webm'):
                return {
                    'success': False,
                    'error': (
                        f"WhatsApp Cloud API does not support '{filename}' ({mime_type}).\n"
                        f"WhatsApp only supports MP4 (.mp4) and 3GP (.3gp) video formats (max 16 MB).\n"
                        f"Please convert or save the video as MP4 (H.264 video + AAC audio) before sending."
                    )
                }
            elif mime_type.startswith('audio/'):
                return {
                    'success': False,
                    'error': (
                        f"WhatsApp Cloud API does not support '{filename}' ({mime_type}).\n"
                        f"WhatsApp only supports MP3 (audio/mpeg), AAC, M4A, AMR, and OGG/OPUS audio (max 16 MB)."
                    )
                }
            elif mime_type.startswith('image/'):
                return {
                    'success': False,
                    'error': (
                        f"WhatsApp Cloud API does not support '{filename}' ({mime_type}).\n"
                        f"WhatsApp only supports JPEG, PNG, and WEBP images (max 5 MB)."
                    )
                }
            else:
                return {
                    'success': False,
                    'error': (
                        f"WhatsApp Cloud API does not support uploading '{filename}' of type '{mime_type}'.\n\n"
                        f"Allowed formats on WhatsApp are:\n"
                        f"• Documents: PDF, Word (DOC/DOCX), Excel (XLS/XLSX), PowerPoint (PPT/PPTX), TXT\n"
                        f"• Videos: MP4, 3GP (max 16 MB)\n"
                        f"• Images: JPEG, PNG, WEBP (max 5 MB)\n"
                        f"• Audio: MP3, AAC, M4A, AMR, OGG (max 16 MB)"
                    )
                }

        url = f'https://graph.facebook.com/{self.GRAPH_API_VERSION}/{self.provider.phone_number_id}/media'
        headers = {
            'Authorization': f'Bearer {self.provider.access_token}',
        }
        if isinstance(file_content, str):
            try:
                import base64
                file_content = base64.b64decode(file_content)
            except Exception:
                file_content = file_content.encode('utf-8')

        files = {
            'file': (filename or 'document.pdf', file_content, mime_type),
        }
        data = {
            'messaging_product': 'whatsapp',
            'type': mime_type,
        }

        try:
            resp = requests.post(url, headers=headers, data=data, files=files, timeout=45)
            if resp is not None and resp.status_code in (200, 201):
                res_data = resp.json()
                media_id = res_data.get('id')
                if media_id:
                    return {'success': True, 'media_id': media_id}
            error = self.parse_error(self._extract_response_json(resp))
            return {'success': False, 'error': f"Failed to upload document to WhatsApp: {error}"}
        except Exception as e:
            _logger.error('Meta media upload error: %s', e, exc_info=True)
            return {'success': False, 'error': str(e)}

    def send_document_message(self, phone, file_content=None, filename=None, media_id=None,
                              caption=None, template_name=None, language=None, mime_type=None, **kwargs):
        """Send a PDF, document, image, or video via Meta Cloud API."""
        phone = self.normalize_phone(phone)
        if not phone:
            return {'success': False, 'error': 'Invalid phone number.'}

        to_number = phone.lstrip('+')

        if not mime_type or mime_type == 'application/octet-stream':
            import mimetypes
            if filename:
                mime_type = mimetypes.guess_type(filename)[0]
        mime_type = (mime_type or 'application/pdf').lower()

        if self.provider.sandbox_mode:
            _logger.info('MetaCloudAdapter SANDBOX document message: %s to %s', filename, to_number)
            return {
                'success': True,
                'provider_message_id': f'meta-sandbox-doc-{to_number}',
            }

        # If media_id not supplied, upload the file first
        if not media_id and file_content:
            upload_res = self.upload_media(file_content, filename or 'document.pdf', mime_type)
            if not upload_res.get('success'):
                return upload_res
            media_id = upload_res.get('media_id')

        if not media_id:
            return {'success': False, 'error': 'No document media ID available.'}

        url = f'https://graph.facebook.com/{self.GRAPH_API_VERSION}/{self.provider.phone_number_id}/messages'

        if template_name:
            # Template with document header
            payload = {
                'messaging_product': 'whatsapp',
                'to': to_number,
                'type': 'template',
                'template': {
                    'name': template_name,
                    'language': {'code': language or 'en'},
                    'components': [
                        {
                            'type': 'header',
                            'parameters': [
                                {
                                    'type': 'document',
                                    'document': {
                                        'id': media_id,
                                        'filename': filename or 'document.pdf',
                                    }
                                }
                            ]
                        }
                    ]
                }
            }
        else:
            # Freeform media message (image, video, audio, or document)
            if mime_type.startswith('image/'):
                msg_type = 'image'
            elif mime_type in ('video/mp4', 'video/3gpp') or mime_type.startswith('video/'):
                msg_type = 'video'
            elif mime_type.startswith('audio/'):
                msg_type = 'audio'
            else:
                msg_type = 'document'

            media_obj = {'id': media_id}
            if msg_type == 'document':
                media_obj['filename'] = filename or 'document.pdf'
            if caption and msg_type in ('image', 'video', 'document'):
                media_obj['caption'] = caption

            payload = {
                'messaging_product': 'whatsapp',
                'to': to_number,
                'type': msg_type,
                msg_type: media_obj,
            }

        resp = self._make_request('POST', url, headers=self._headers(), json_data=payload)
        if resp is not None and resp.status_code in (200, 201):
            data = resp.json()
            msg_id = data.get('messages', [{}])[0].get('id', '')
            return {'success': True, 'provider_message_id': msg_id, 'media_id': media_id}
        error = self.parse_error(self._extract_response_json(resp))
        return {'success': False, 'error': error}

    def mark_message_read(self, message_id):
        """Send read receipt to Meta Cloud API to show blue checkmarks to customer."""
        if not message_id:
            return {'success': False, 'error': 'Message ID required.'}
        url = (
            f'https://graph.facebook.com/{self.GRAPH_API_VERSION}'
            f'/{self.provider.phone_number_id}/messages'
        )
        payload = {
            'messaging_product': 'whatsapp',
            'status': 'read',
            'message_id': message_id,
        }
        resp = self._make_request('POST', url, headers=self._headers(), json_data=payload)
        if resp is not None and resp.status_code in (200, 201):
            return {'success': True}
        return {'success': False}

    def _headers(self):
        return {
            'Authorization': f'Bearer {self.provider.access_token}',
            'Content-Type': 'application/json',
        }


# --------------------------------------------------------------------------
# Adapter Registry (Odoo abstract model)
# --------------------------------------------------------------------------

# Map provider_type to adapter class
ADAPTER_REGISTRY = {
    'generic': GenericAdapter,
    'meta_cloud': MetaCloudAdapter,
    # 'twilio': TwilioAdapter,       # future
    # 'gupshup': GupshupAdapter,     # future
    # '360dialog': Dialog360Adapter,  # future
}


class WhatsappProviderAdapterService(models.AbstractModel):
    _name = 'whatsapp.automation.provider.adapter'
    _description = 'WhatsApp Provider Adapter Service'

    def normalize_phone(self, phone):
        """Helper to normalize phone numbers."""
        return normalize_phone(phone)

    def get_adapter(self, provider_record):
        """Return the appropriate adapter instance for the given provider."""
        adapter_cls = ADAPTER_REGISTRY.get(provider_record.provider_type, GenericAdapter)
        return adapter_cls(provider_record)

    def send_message(self, provider_record, phone, template=None, body=None,
                     template_name=None, language=None, variables=None, **kwargs):
        """
        Unified send method. Uses template or text depending on args.
        Returns dict: {success, provider_message_id, error}
        """
        adapter = self.get_adapter(provider_record)
        phone = adapter.normalize_phone(phone)
        if not phone:
            return {'success': False, 'error': 'Invalid or missing phone number.'}

        if template_name:
            return adapter.send_template_message(
                phone, template_name, language or 'en', variables=variables, **kwargs,
            )
        elif body:
            return adapter.send_text_message(phone, body, **kwargs)
        else:
            return {'success': False, 'error': 'No template or body provided.'}

    def send_document(self, provider_record, phone, file_content, filename,
                      caption=None, template_name=None, language=None, mime_type=None, **kwargs):
        """
        Unified send document method. Uploads file and sends document message.
        Returns dict: {success, provider_message_id, error}
        """
        adapter = self.get_adapter(provider_record)
        return adapter.send_document_message(
            phone=phone,
            file_content=file_content,
            filename=filename,
            caption=caption,
            template_name=template_name,
            language=language,
            mime_type=mime_type,
            **kwargs,
        )

    def mark_message_read(self, provider_record, message_id):
        """Send read receipt via provider adapter."""
        adapter = self.get_adapter(provider_record)
        if hasattr(adapter, 'mark_message_read'):
            return adapter.mark_message_read(message_id)
        return {'success': False}
