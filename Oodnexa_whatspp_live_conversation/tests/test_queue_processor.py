"""
Tests for Queue Processor Service
===================================
Verifies batch acquisition, message sending, log cleanup,
and error handling.
"""
from datetime import timedelta
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo import fields


class TestQueueProcessor(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))

        # Category
        cls.category = cls.env['whatsapp.automation.category'].create({
            'name': 'Queue Test Cat',
            'code': 'QT_CAT',
        })

        # Provider
        cls.provider = cls.env['whatsapp.automation.provider'].create({
            'name': 'Queue Test Provider',
            'provider_type': 'generic',
            'api_base_url': 'https://api.example.com/v1',
            'sandbox_mode': True,
            'default_provider': True,
            'status': 'active',
        })

        # Template
        cls.template = cls.env['whatsapp.automation.template'].create({
            'name': 'Queue Test Template',
            'template_type': 'text',
            'template_category': 'utility',
            'category_id': cls.category.id,
            'body_text': 'Hello {{customer_name}}!',
            'approval_status': 'approved',
        })

        # Partner
        cls.partner = cls.env['res.partner'].create({
            'name': 'Queue Test Customer',
            'mobile': '+1234567890',
            'wa_opt_in': True,
        })

        cls.processor = cls.env['whatsapp.automation.queue.processor']

    def _create_queued_message(self, **overrides):
        """Helper to create a message + queue item pair."""
        msg_vals = {
            'company_id': self.env.company.id,
            'provider_id': self.provider.id,
            'partner_id': self.partner.id,
            'phone': '+1234567890',
            'template_id': self.template.id,
            'state': 'queued',
            'rendered_body': 'Hello Queue Test Customer!',
            'max_retries': 3,
        }
        msg_vals.update(overrides)
        message = self.env['whatsapp.automation.message'].create(msg_vals)

        queue_item = self.env['whatsapp.automation.queue'].create({
            'message_id': message.id,
            'scheduled_for': fields.Datetime.now(),
            'state': 'pending',
            'max_attempts': 3,
        })
        return message, queue_item

    # ------------------------------------------------------------------
    # Queue Acquisition
    # ------------------------------------------------------------------

    def test_acquire_batch_returns_pending_items(self):
        """_acquire_batch should return pending/scheduled items."""
        msg, qi = self._create_queued_message()
        queue_model = self.env['whatsapp.automation.queue']
        batch = queue_model._acquire_batch(batch_size=10)
        self.assertTrue(len(batch) >= 1, 'Should acquire at least one queue item')

    def test_acquire_batch_respects_size(self):
        """Batch size should limit the number of acquired items."""
        for _ in range(5):
            self._create_queued_message()

        queue_model = self.env['whatsapp.automation.queue']
        batch = queue_model._acquire_batch(batch_size=2)
        self.assertLessEqual(len(batch), 2)

    def test_acquire_batch_skips_locked(self):
        """Items locked by another process should not be acquired."""
        msg, qi = self._create_queued_message()
        # Simulate lock
        qi.write({
            'state': 'processing',
            'lock_expires_at': fields.Datetime.to_string(
                fields.Datetime.from_string(fields.Datetime.now()) + timedelta(hours=1)
            ),
        })

        queue_model = self.env['whatsapp.automation.queue']
        batch = queue_model._acquire_batch(batch_size=10)
        acquired_ids = batch.ids
        self.assertNotIn(qi.id, acquired_ids, 'Locked item should not be acquired')

    # ------------------------------------------------------------------
    # Process Queue
    # ------------------------------------------------------------------

    def test_process_queue_sends_messages(self):
        """Process queue should call send_message and update states."""
        msg, qi = self._create_queued_message()

        mock_result = {'success': True, 'provider_message_id': 'test_123'}

        with patch.object(
            type(self.env['whatsapp.automation.provider.adapter']),
            'send_message',
            return_value=mock_result,
        ):
            self.processor.process_queue()

        msg.invalidate_recordset()
        qi.invalidate_recordset()

        self.assertEqual(msg.state, 'sent', 'Message should be marked sent')
        self.assertEqual(qi.state, 'done', 'Queue item should be done')
        self.assertEqual(msg.provider_message_id, 'test_123')

    def test_process_queue_handles_failure(self):
        """Failed sends should mark message & queue as failed."""
        msg, qi = self._create_queued_message()

        with patch.object(
            type(self.env['whatsapp.automation.provider.adapter']),
            'send_message',
            return_value={'success': False, 'error': 'API Error'},
        ):
            self.processor.process_queue()

        msg.invalidate_recordset()
        qi.invalidate_recordset()

        self.assertEqual(msg.state, 'failed')

    def test_process_queue_empty_noop(self):
        """Processing with empty queue should not raise."""
        # Clear any leftover queue items
        self.env['whatsapp.automation.queue'].search([]).unlink()
        self.processor.process_queue()  # Should not raise

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def test_cleanup_old_logs(self):
        """cleanup_old_logs should remove logs older than retention period."""
        self.env['ir.config_parameter'].sudo().set_param(
            'Oodnexa_whatspp_live_conversation.log_retention_days', '1'
        )

        # Create an old log
        log = self.env['whatsapp.automation.log'].create({
            'event_type': 'queued',
            'description': 'Test old log',
        })
        # Manually set create_date to 5 days ago
        five_days_ago = fields.Datetime.to_string(
            fields.Datetime.from_string(fields.Datetime.now()) - timedelta(days=5)
        )
        self.env.cr.execute(
            "UPDATE whatsapp_automation_log SET create_date = %s WHERE id = %s",
            (five_days_ago, log.id),
        )
        log.invalidate_recordset()

        self.processor.cleanup_old_logs()

        self.assertFalse(
            self.env['whatsapp.automation.log'].search([('id', '=', log.id)]),
            'Old log should be deleted',
        )

    def test_cleanup_preserves_recent_logs(self):
        """cleanup_old_logs should keep recent logs."""
        self.env['ir.config_parameter'].sudo().set_param(
            'Oodnexa_whatspp_live_conversation.log_retention_days', '30'
        )

        log = self.env['whatsapp.automation.log'].create({
            'event_type': 'sent',
            'description': 'Recent log',
        })

        self.processor.cleanup_old_logs()

        self.assertTrue(
            self.env['whatsapp.automation.log'].search([('id', '=', log.id)]),
            'Recent log should be preserved',
        )

    # ------------------------------------------------------------------
    # Template Variable Extraction
    # ------------------------------------------------------------------

    def test_extract_template_variables(self):
        """Should extract variables from template schema for provider."""
        msg, _ = self._create_queued_message()
        msg.write({
            'related_model': 'res.partner',
            'related_res_id': self.partner.id,
        })

        result = self.processor._extract_template_variables(msg)
        self.assertIsInstance(result, list)
