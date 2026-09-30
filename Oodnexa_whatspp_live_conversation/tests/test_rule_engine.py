"""
Tests for Automation Rule Engine
=================================
Verifies rule evaluation, event-based triggers, date reminders,
duplicate prevention, partner/phone extraction, and message creation.
"""
from datetime import timedelta
from unittest.mock import patch
from odoo.tests.common import TransactionCase
from odoo import fields


class TestRuleEngine(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))

        # Enable the engine
        cls.env['ir.config_parameter'].sudo().set_param(
            'Oodnexa_whatspp_live_conversation.enabled', 'True'
        )

        # Category
        cls.category = cls.env['whatsapp.automation.category'].create({
            'name': 'Test Cat',
            'code': 'T_CAT',
        })

        # Provider
        cls.provider = cls.env['whatsapp.automation.provider'].create({
            'name': 'Test Provider',
            'provider_type': 'generic',
            'api_base_url': 'https://api.example.com/v1',
            'sandbox_mode': True,
            'default_provider': True,
            'status': 'active',
        })

        # Template
        cls.template = cls.env['whatsapp.automation.template'].create({
            'name': 'Test Rule Template',
            'template_type': 'text',
            'template_category': 'utility',
            'category_id': cls.category.id,
            'body_text': 'Hello {{customer_name}}, from {{company_name}}.',
            'approval_status': 'approved',
        })

        # Partner
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Customer',
            'mobile': '+1234567890',
            'wa_opt_in': True,
        })

        cls.engine = cls.env['whatsapp.automation.rule.engine']

    def _make_rule(self, **overrides):
        """Helper to create a rule with sensible defaults."""
        vals = {
            'name': 'Test Rule',
            'code': 'TEST_RULE',
            'category_id': self.category.id,
            'target_model_id': self.env.ref('contacts.model_res_partner').id,
            'trigger_type': 'on_create',
            'delay_mode': 'immediate',
            'template_id': self.template.id,
            'recipient_mode': 'partner_mobile',
            'state': 'active',
        }
        vals.update(overrides)
        return self.env['whatsapp.automation.rule'].create(vals)

    # ------------------------------------------------------------------
    # Engine Enable/Disable
    # ------------------------------------------------------------------

    def test_engine_disabled(self):
        """When engine is disabled, run_all_active_rules should be a no-op."""
        self.env['ir.config_parameter'].sudo().set_param(
            'Oodnexa_whatspp_live_conversation.enabled', 'False'
        )
        rule = self._make_rule()
        self.engine.run_all_active_rules()
        # No messages created
        count = self.env['whatsapp.automation.message'].search_count([
            ('rule_id', '=', rule.id),
        ])
        self.assertEqual(count, 0)
        # Re-enable
        self.env['ir.config_parameter'].sudo().set_param(
            'Oodnexa_whatspp_live_conversation.enabled', 'True'
        )

    # ------------------------------------------------------------------
    # Rule Evaluation
    # ------------------------------------------------------------------

    def test_evaluate_rule_creates_messages(self):
        """Active rule matching records should create queued messages."""
        rule = self._make_rule(
            domain_filter="[('id', '=', %d)]" % self.partner.id,
        )
        self.engine.run_all_active_rules()

        messages = self.env['whatsapp.automation.message'].search([
            ('rule_id', '=', rule.id),
        ])
        self.assertTrue(len(messages) >= 1, 'Should create at least one message')
        self.assertEqual(messages[0].state, 'queued')

    def test_evaluate_rule_skips_ineligible(self):
        """Partners who are blacklisted should not get messages."""
        self.partner.write({'wa_blacklisted': True})
        self.addCleanup(lambda: self.partner.write({'wa_blacklisted': False}))

        rule = self._make_rule(
            domain_filter="[('id', '=', %d)]" % self.partner.id,
        )
        self.engine.run_all_active_rules()

        count = self.env['whatsapp.automation.message'].search_count([
            ('rule_id', '=', rule.id),
        ])
        self.assertEqual(count, 0, 'Blacklisted partner should not receive messages')

    def test_duplicate_prevention(self):
        """allow_resend=False should prevent sending twice to same record."""
        rule = self._make_rule(
            domain_filter="[('id', '=', %d)]" % self.partner.id,
            allow_resend=False,
        )

        # First run
        self.engine.run_all_active_rules()
        count1 = self.env['whatsapp.automation.message'].search_count([
            ('rule_id', '=', rule.id),
        ])

        # Second run
        self.engine.run_all_active_rules()
        count2 = self.env['whatsapp.automation.message'].search_count([
            ('rule_id', '=', rule.id),
        ])

        self.assertEqual(count1, count2, 'Should not create duplicate message')

    # ------------------------------------------------------------------
    # Event Rules
    # ------------------------------------------------------------------

    def test_event_rules_on_create(self):
        """Event rules for on_create should match newly created records."""
        rule = self._make_rule(trigger_type='on_create')

        new_partner = self.env['res.partner'].create({
            'name': 'New Customer',
            'mobile': '+9876543210',
            'wa_opt_in': True,
        })
        self.engine.run_event_rules('res.partner', 'on_create', new_partner)

        count = self.env['whatsapp.automation.message'].search_count([
            ('rule_id', '=', rule.id),
            ('related_res_id', '=', new_partner.id),
        ])
        self.assertTrue(count >= 1, 'on_create event rule should produce a message')

    # ------------------------------------------------------------------
    # Test Rule (Preview)
    # ------------------------------------------------------------------

    def test_test_rule_method(self):
        """test_rule should return preview info without creating messages."""
        rule = self._make_rule(
            domain_filter="[('id', '=', %d)]" % self.partner.id,
        )
        msg_count_before = self.env['whatsapp.automation.message'].search_count([])
        result = self.engine.test_rule(rule)
        msg_count_after = self.env['whatsapp.automation.message'].search_count([])

        # test_rule should NOT create messages
        self.assertEqual(msg_count_before, msg_count_after)
        self.assertTrue(result.get('success'))

    def test_test_rule_no_match(self):
        """test_rule with impossible domain should report no matches."""
        rule = self._make_rule(
            domain_filter="[('id', '=', -1)]",
        )
        result = self.engine.test_rule(rule)
        self.assertFalse(result.get('success'))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def test_get_partner_from_partner_record(self):
        """_get_partner should return the record itself for res.partner."""
        rule = self._make_rule()
        partner = self.engine._get_partner(rule, self.partner)
        self.assertEqual(partner.id, self.partner.id)

    def test_get_phone_partner_mobile(self):
        """_get_phone should return normalized mobile for partner_mobile mode."""
        rule = self._make_rule(recipient_mode='partner_mobile')
        phone = self.engine._get_phone(rule, self.partner, self.partner)
        self.assertTrue(phone, 'Phone should not be empty')
        self.assertTrue(phone.startswith('+'), 'Phone should start with +')

    def test_enqueue_with_delay(self):
        """Messages with delay should be scheduled in the future."""
        rule = self._make_rule(
            delay_mode='delay',
            delay_value=2,
            delay_unit='hours',
            domain_filter="[('id', '=', %d)]" % self.partner.id,
        )
        self.engine.run_all_active_rules()

        queue_items = self.env['whatsapp.automation.queue'].search([
            ('message_id.rule_id', '=', rule.id),
        ])
        if queue_items:
            now = fields.Datetime.from_string(fields.Datetime.now())
            for qi in queue_items:
                sched = fields.Datetime.from_string(qi.scheduled_for)
                self.assertGreater(sched, now, 'Scheduled time should be in the future')
