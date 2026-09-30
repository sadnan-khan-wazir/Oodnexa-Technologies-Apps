"""
Automation Rule Engine
======================
Evaluates automation rules, finds matching records, creates queued messages.
Invoked by cron jobs and by write/create hooks on monitored models.
"""
import json
import logging
from datetime import timedelta
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class WhatsappAutomationRuleEngine(models.AbstractModel):
    _name = 'whatsapp.automation.rule.engine'
    _description = 'WhatsApp Automation Rule Engine'

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_all_active_rules(self):
        """Cron entry point: evaluate all active rules."""
        if not self._is_engine_enabled():
            _logger.info('WhatsApp automation engine is disabled.')
            return

        rules = self.env['whatsapp.automation.rule'].search([
            ('state', '=', 'active'),
            ('active', '=', True),
        ], order='sequence, priority desc')

        for rule in rules:
            try:
                self._evaluate_rule(rule)
            except Exception as e:
                _logger.error('Error evaluating rule %s: %s', rule.name, e, exc_info=True)

    def run_event_rules(self, model_name, trigger_type, records, changed_fields=None):
        """
        Called from model hooks (create/write) to evaluate event-based rules.

        :param model_name: e.g. 'crm.lead'
        :param trigger_type: e.g. 'on_create', 'on_write', 'on_state_change'
        :param records: recordset of affected records
        :param changed_fields: list of field names that changed (for on_write)
        """
        if not self._is_engine_enabled():
            return

        rules = self.env['whatsapp.automation.rule'].search([
            ('state', '=', 'active'),
            ('active', '=', True),
            ('target_model_name', '=', model_name),
            ('trigger_type', '=', trigger_type),
        ], order='sequence, priority desc')

        for rule in rules:
            try:
                # For on_write, check if watched fields overlap
                if trigger_type in ('on_write', 'on_state_change') and changed_fields:
                    watched = rule.watched_field_ids.mapped('name')
                    if watched and not set(changed_fields) & set(watched):
                        continue

                self._evaluate_rule(rule, candidate_records=records)
            except Exception as e:
                _logger.error('Error evaluating event rule %s: %s', rule.name, e, exc_info=True)

    def run_date_reminder_rules(self):
        """Cron entry point: evaluate date-based reminder rules."""
        if not self._is_engine_enabled():
            return

        rules = self.env['whatsapp.automation.rule'].search([
            ('state', '=', 'active'),
            ('active', '=', True),
            ('trigger_type', '=', 'date_reminder'),
            ('date_field_id', '!=', False),
        ])

        for rule in rules:
            try:
                self._evaluate_date_reminder(rule)
            except Exception as e:
                _logger.error('Error in date reminder rule %s: %s', rule.name, e, exc_info=True)

    def test_rule(self, rule):
        """Test a rule without sending. Return preview info."""
        domain = rule._get_domain()
        Model = self.env[rule.target_model_name]
        records = Model.search(domain, limit=5)

        if not records:
            return {
                'success': False,
                'message': _('No matching records found for domain: %s', rule.domain_filter),
            }

        renderer = self.env['whatsapp.automation.template.renderer']
        previews = []
        for rec in records[:3]:
            partner = self._get_partner(rule, rec)
            phone = self._get_phone(rule, rec, partner)
            body = renderer.render_body_only(rule.template_id, record=rec, partner=partner)
            previews.append(f'{rec.display_name} → {phone}: {body[:80]}...')

        return {
            'success': True,
            'message': _('%d matching records. Previews:\n%s',
                         len(records), '\n'.join(previews)),
        }

    # ------------------------------------------------------------------
    # Internal: Rule Evaluation
    # ------------------------------------------------------------------

    def _evaluate_rule(self, rule, candidate_records=None):
        """Evaluate a single rule, optionally against specific records."""
        Model = self.env[rule.target_model_name]
        domain = rule._get_domain()

        if candidate_records is not None:
            records = candidate_records.filtered_domain(domain)
        else:
            records = Model.search(domain)

        if not records:
            return

        renderer = self.env['whatsapp.automation.template.renderer']
        created_count = 0
        failed_count = 0

        for record in records:
            try:
                if not self._should_send(rule, record):
                    continue
                partner = self._get_partner(rule, record)
                phone = self._get_phone(rule, record, partner)
                if not phone:
                    _logger.debug('Rule %s: no phone for record %s', rule.name, record.display_name)
                    continue
                if partner and not partner._is_wa_eligible():
                    _logger.debug('Rule %s: partner %s not eligible', rule.name, partner.name)
                    continue

                rendered = renderer.render(rule.template_id, record=record, partner=partner)
                message = self._create_message(rule, record, partner, phone, rendered)
                self._enqueue_message(rule, message)
                created_count += 1
            except Exception as e:
                failed_count += 1
                _logger.error('Rule %s failed for record %s: %s', rule.name, record, e)

        # Update rule stats
        rule.sudo().write({
            'execution_count': rule.execution_count + 1,
            'success_count': rule.success_count + created_count,
            'failure_count': rule.failure_count + failed_count,
            'last_run_at': fields.Datetime.now(),
        })

    def _evaluate_date_reminder(self, rule):
        """Evaluate a date-reminder rule by checking the date field."""
        Model = self.env[rule.target_model_name]
        domain = rule._get_domain()

        field_name = rule.date_field_id.name
        now = fields.Datetime.now()
        delta = timedelta(**{rule.delay_unit: rule.delay_value})

        if rule.date_field_direction == 'before':
            target_start = now
            target_end = fields.Datetime.to_string(
                fields.Datetime.from_string(now) + delta
            )
            domain += [(field_name, '>=', target_start), (field_name, '<=', target_end)]
        else:
            target_date = fields.Datetime.to_string(
                fields.Datetime.from_string(now) - delta
            )
            domain += [(field_name, '<=', target_date)]

        records = Model.search(domain)
        if records:
            self._evaluate_rule(rule, candidate_records=records)

    # ------------------------------------------------------------------
    # Internal: Helpers
    # ------------------------------------------------------------------

    def _should_send(self, rule, record):
        """Check duplicate prevention and max send count."""
        if rule.allow_resend:
            if rule.max_send_count > 0:
                count = self.env['whatsapp.automation.message'].search_count([
                    ('rule_id', '=', rule.id),
                    ('related_model', '=', rule.target_model_name),
                    ('related_res_id', '=', record.id),
                    ('state', 'not in', ('cancelled', 'failed')),
                ])
                return count < rule.max_send_count
            return True

        # Not allow_resend: check if already sent (non-failed)
        exists = self.env['whatsapp.automation.message'].search_count([
            ('rule_id', '=', rule.id),
            ('related_model', '=', rule.target_model_name),
            ('related_res_id', '=', record.id),
            ('state', 'not in', ('cancelled', 'failed')),
        ])
        if exists:
            return False

        # Duplicate window check
        window_hours = int(self.env['ir.config_parameter'].sudo().get_param(
            'Oodnexa_whatspp_live_conversation.duplicate_window_hours', '24'
        ))
        if window_hours > 0:
            cutoff = fields.Datetime.to_string(
                fields.Datetime.from_string(fields.Datetime.now()) - timedelta(hours=window_hours)
            )
            recent = self.env['whatsapp.automation.message'].search_count([
                ('rule_id', '=', rule.id),
                ('related_model', '=', rule.target_model_name),
                ('related_res_id', '=', record.id),
                ('create_date', '>=', cutoff),
            ])
            if recent:
                return False

        return True

    def _get_partner(self, rule, record):
        """Extract partner from record based on rule config."""
        if hasattr(record, 'partner_id') and record.partner_id:
            return record.partner_id
        if record._name == 'res.partner':
            return record
        # Try common partner fields
        for field in ('customer_id', 'partner_shipping_id', 'commercial_partner_id'):
            if hasattr(record, field):
                partner = getattr(record, field)
                if partner:
                    return partner
        return self.env['res.partner']

    def _get_phone(self, rule, record, partner):
        """Get phone number based on recipient_mode and phone_field_name."""
        from ..services.provider_adapter import normalize_phone

        if rule.recipient_mode == 'record_field' and rule.phone_field_name:
            raw = self._safe_getattr(record, rule.phone_field_name)
            if raw:
                return normalize_phone(str(raw))

        if partner:
            p_phone = getattr(partner, 'phone', False) or ''
            p_mobile = getattr(partner, 'mobile', False) or ''
            if rule.recipient_mode == 'partner_mobile':
                return normalize_phone(p_mobile or p_phone)
            elif rule.recipient_mode == 'partner_phone':
                return normalize_phone(p_phone or p_mobile)
            return normalize_phone(p_phone or p_mobile)
        return ''

    def _safe_getattr(self, record, field_path):
        """Safely get a dotted field path from a record."""
        try:
            obj = record
            for part in field_path.split('.'):
                if not hasattr(obj, part):
                    return None
                obj = getattr(obj, part)
            return obj
        except Exception:
            return None

    def _create_message(self, rule, record, partner, phone, rendered):
        """Create a whatsapp.automation.message."""
        return self.env['whatsapp.automation.message'].create({
            'company_id': rule.company_id.id,
            'provider_id': rule._get_effective_provider().id,
            'partner_id': partner.id if partner else False,
            'phone': phone,
            'related_model': rule.target_model_name,
            'related_res_id': record.id,
            'rule_id': rule.id,
            'template_id': rule.template_id.id,
            'campaign_id': rule.campaign_id.id if rule.campaign_id else False,
            'state': 'draft',
            'message_type': 'template' if rule.template_id.template_type == 'template' else 'reminder',
            'rendered_body': rendered.get('body', ''),
            'max_retries': int(self.env['ir.config_parameter'].sudo().get_param(
                'Oodnexa_whatspp_live_conversation.retry_attempts', '3'
            )),
        })

    def _enqueue_message(self, rule, message):
        """Create queue entry for a message with scheduling logic."""
        now = fields.Datetime.now()
        scheduled_for = now

        if rule.delay_mode == 'delay' and rule.delay_value > 0:
            delta = timedelta(**{rule.delay_unit: rule.delay_value})
            scheduled_for = fields.Datetime.to_string(
                fields.Datetime.from_string(now) + delta
            )
        elif rule.delay_mode == 'fixed_time' and rule.schedule_datetime:
            scheduled_for = rule.schedule_datetime

        self.env['whatsapp.automation.queue'].create({
            'message_id': message.id,
            'scheduled_for': scheduled_for,
            'state': 'pending' if scheduled_for == now else 'scheduled',
            'max_attempts': message.max_retries,
        })
        message.write({
            'state': 'queued',
            'queued_at': now,
        })
        message._log_event('queued', f'Scheduled for {scheduled_for}')

    def _is_engine_enabled(self):
        """Check global enable flag."""
        return self.env['ir.config_parameter'].sudo().get_param(
            'Oodnexa_whatspp_live_conversation.enabled', 'True'
        ) in ('True', 'true', '1')
