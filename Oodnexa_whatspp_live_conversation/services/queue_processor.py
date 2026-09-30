"""
Queue Processor
===============
Processes the pending message queue in batches.
Called by cron job. Sends messages through the provider adapter.
"""
import json
import logging
from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class WhatsappQueueProcessorService(models.AbstractModel):
    _name = 'whatsapp.automation.queue.processor'
    _description = 'WhatsApp Queue Processor'

    def process_queue(self):
        """Cron entry point: process pending messages in the queue."""
        batch_size = int(self.env['ir.config_parameter'].sudo().get_param(
            'Oodnexa_whatspp_live_conversation.queue_batch_size', '50'
        ))

        queue_model = self.env['whatsapp.automation.queue']
        items = queue_model._acquire_batch(batch_size=batch_size)

        if not items:
            return

        _logger.info('Processing %d queued WhatsApp messages.', len(items))

        adapter_service = self.env['whatsapp.automation.provider.adapter']

        for item in items:
            message = item.message_id
            if not message.exists():
                item._mark_done()
                continue

            try:
                self._process_single(adapter_service, message, item)
            except Exception as e:
                _logger.error('Queue item %d failed: %s', item.id, e, exc_info=True)
                item._mark_failed(str(e))
                message.write({
                    'state': 'failed',
                    'error_message': str(e)[:500],
                    'failed_at': fields.Datetime.now(),
                    'retry_count': message.retry_count + 1,
                })
                message._log_event('failed', str(e)[:500])

    def _process_single(self, adapter_service, message, queue_item):
        """Process a single message from the queue."""
        # Mark processing
        message.write({'state': 'processing'})
        message._log_event('processing')

        # Get provider
        provider = message.provider_id
        if not provider:
            # Try rule's effective provider
            if message.rule_id:
                provider = message.rule_id._get_effective_provider()
            if not provider:
                raise ValueError('No provider configured for message.')

        # Determine send method
        template = message.template_id
        result = {}

        if template and template.template_type == 'template' and template.provider_template_id:
            # Send via provider template
            variables = self._extract_template_variables(message)
            result = adapter_service.send_message(
                provider,
                phone=message.phone,
                template_name=template.provider_template_id,
                language=template.language_code or 'en',
                variables=variables,
            )
        else:
            # Send rendered body as text
            result = adapter_service.send_message(
                provider,
                phone=message.phone,
                body=message.rendered_body or '',
            )

        # Process result
        if result.get('success'):
            now = fields.Datetime.now()
            message.write({
                'state': 'sent',
                'sent_at': now,
                'provider_message_id': result.get('provider_message_id', ''),
                'provider_id': provider.id,
                'payload_json': json.dumps(result, default=str),
            })
            message._log_event('sent', f"Provider ID: {result.get('provider_message_id', '')}")
            queue_item._mark_done()

            # Update contact preference stats
            if message.partner_id:
                pref = self.env['whatsapp.automation.contact.preference'].search([
                    ('partner_id', '=', message.partner_id.id),
                ], limit=1)
                if pref:
                    pref.write({
                        'last_message_at': now,
                        'total_messages_sent': pref.total_messages_sent + 1,
                    })
        else:
            error = result.get('error', 'Unknown error')
            raise ValueError(error)

    def _extract_template_variables(self, message):
        """Extract ordered variable values for provider template."""
        if not message.template_id or not message.template_id.variable_schema_json:
            return []

        try:
            schema = json.loads(message.template_id.variable_schema_json)
        except (json.JSONDecodeError, TypeError):
            return []

        # Get the rendered body and try to match variable order
        renderer = self.env['whatsapp.automation.template.renderer']
        record = None
        if message.related_model and message.related_res_id:
            try:
                record = self.env[message.related_model].browse(message.related_res_id)
                if not record.exists():
                    record = None
            except Exception:
                record = None

        if record:
            rendered = renderer.render(
                message.template_id,
                record=record,
                partner=message.partner_id,
            )
            return list(rendered.get('variable_values', {}).values())

        return []

    def cleanup_old_logs(self):
        """Cron entry point: remove old logs based on retention period."""
        retention_days = int(self.env['ir.config_parameter'].sudo().get_param(
            'Oodnexa_whatspp_live_conversation.log_retention_days', '90'
        ))
        if retention_days <= 0:
            return
        from datetime import timedelta
        cutoff = fields.Datetime.to_string(
            fields.Datetime.from_string(fields.Datetime.now()) - timedelta(days=retention_days)
        )
        old_logs = self.env['whatsapp.automation.log'].search([
            ('create_date', '<', cutoff),
        ])
        if old_logs:
            _logger.info('Cleaning up %d old WhatsApp automation logs.', len(old_logs))
            old_logs.unlink()
