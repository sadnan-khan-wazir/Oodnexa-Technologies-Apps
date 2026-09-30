import json
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

TRIGGER_TYPES = [
    ('on_create', 'Record Created'),
    ('on_write', 'Record Updated'),
    ('on_state_change', 'State Changed'),
    ('on_stage_change', 'Stage Changed'),
    ('date_reminder', 'Date Reminder'),
    ('scheduled_batch', 'Scheduled Batch Run'),
    ('manual', 'Manual Send'),
    ('follow_up', 'Follow-up After Delay'),
    ('inactivity', 'Inactivity Detection'),
]

DELAY_UNITS = [
    ('minutes', 'Minutes'),
    ('hours', 'Hours'),
    ('days', 'Days'),
]

SCHEDULE_TYPES = [
    ('immediate', 'Immediately'),
    ('delay', 'After Delay'),
    ('fixed_time', 'At Fixed Date/Time'),
    ('date_field', 'Relative to Date Field'),
]

RULE_STATES = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('paused', 'Paused'),
    ('archived', 'Archived'),
]

RECIPIENT_MODES = [
    ('partner_phone', 'Partner Phone'),
    ('partner_mobile', 'Partner Mobile'),
    ('record_field', 'Custom Field on Record'),
]


class WhatsappAutomationRule(models.Model):
    _name = 'whatsapp.automation.rule'
    _description = 'WhatsApp Automation Rule'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, priority desc, name'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    description = fields.Text()
    code = fields.Char(
        string='Rule Code',
        help='Unique short code for this rule.',
        index=True,
    )
    category_id = fields.Many2one(
        'whatsapp.automation.category', string='Category',
    )
    color = fields.Integer()

    # Target Model
    target_model_id = fields.Many2one(
        'ir.model', string='Target Model',
        required=True,
        ondelete='cascade',
        domain=[('transient', '=', False)],
    )
    target_model_name = fields.Char(
        related='target_model_id.model', store=True, string='Model Name',
    )

    # Trigger
    trigger_type = fields.Selection(
        selection=TRIGGER_TYPES, required=True, default='on_create',
        tracking=True,
    )
    event_type = fields.Char(
        help='Optional sub-event type, e.g. specific field name for on_write triggers.',
    )
    watched_field_ids = fields.Many2many(
        'ir.model.fields',
        string='Watched Fields',
        domain="[('model_id', '=', target_model_id)]",
        help='Fields that trigger the rule on change (for on_write/on_state_change).',
    )

    # Delay / Schedule
    delay_mode = fields.Selection(
        selection=SCHEDULE_TYPES, default='immediate',
    )
    delay_value = fields.Integer(string='Delay Value', default=0)
    delay_unit = fields.Selection(
        selection=DELAY_UNITS, default='minutes',
    )
    schedule_datetime = fields.Datetime(
        string='Fixed Schedule Time',
        help='Exact date/time to send (for fixed_time mode).',
    )
    date_field_id = fields.Many2one(
        'ir.model.fields',
        string='Date Field',
        domain="[('model_id', '=', target_model_id), ('ttype', 'in', ['date', 'datetime'])]",
        help='Date field on the target record for relative scheduling.',
    )
    date_field_direction = fields.Selection(
        selection=[('before', 'Before'), ('after', 'After')],
        default='before',
        string='Before/After Date',
    )

    # Conditions
    domain_filter = fields.Text(
        string='Domain Filter',
        default='[]',
        help='Odoo domain to filter target records.',
    )
    condition_json = fields.Text(
        string='Extra Conditions (JSON)',
        default='{}',
        help='Additional conditions in JSON format.',
    )

    # Template & Provider
    template_id = fields.Many2one(
        'whatsapp.automation.template', string='Message Template',
        required=True,
    )
    provider_id = fields.Many2one(
        'whatsapp.automation.provider', string='Provider',
        help='Specific provider. Leave empty to use default.',
    )

    # Recipient
    phone_field_name = fields.Char(
        string='Phone Field',
        default='mobile',
        help='Field name on the target model or its partner to get the phone number.',
    )
    recipient_mode = fields.Selection(
        selection=RECIPIENT_MODES, default='partner_mobile',
    )

    # State & Control
    state = fields.Selection(
        selection=RULE_STATES, default='draft', tracking=True,
    )
    allow_resend = fields.Boolean(
        default=False,
        help='Allow sending to the same record multiple times.',
    )
    max_send_count = fields.Integer(
        default=1,
        help='Maximum number of messages to send per record (0 = unlimited if resend allowed).',
    )
    stop_on_reply = fields.Boolean(
        default=False,
        help='Placeholder: stop follow-up sequence when customer replies.',
    )
    priority = fields.Selection(
        selection=[('0', 'Normal'), ('1', 'Low'), ('2', 'High'), ('3', 'Urgent')],
        default='0',
    )
    notes = fields.Html()

    # Campaign link
    campaign_id = fields.Many2one(
        'whatsapp.automation.campaign', string='Campaign',
    )

    # Stats (stored for performance)
    execution_count = fields.Integer(default=0, readonly=True)
    success_count = fields.Integer(default=0, readonly=True)
    failure_count = fields.Integer(default=0, readonly=True)
    last_run_at = fields.Datetime(readonly=True)
    message_count = fields.Integer(compute='_compute_message_count')

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Rule code must be unique.'),
    ]

    def _compute_message_count(self):
        data = self.env['whatsapp.automation.message']._read_group(
            [('rule_id', 'in', self.ids)],
            ['rule_id'],
            ['__count'],
        )
        mapped = {rule.id: count for rule, count in data}
        for rec in self:
            rec.message_count = mapped.get(rec.id, 0)

    @api.constrains('domain_filter')
    def _check_domain_filter(self):
        for rec in self:
            if rec.domain_filter:
                try:
                    domain = safe_eval(rec.domain_filter)
                    if not isinstance(domain, list):
                        raise ValidationError(_('Domain filter must be a valid list.'))
                except Exception:
                    raise ValidationError(_('Invalid domain filter expression.'))

    def action_activate(self):
        for rec in self:
            if not rec.template_id:
                raise UserError(_('A template must be set before activating.'))
            rec.state = 'active'

    def action_pause(self):
        self.write({'state': 'paused'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    def action_archive_rule(self):
        self.write({'state': 'archived', 'active': False})

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Messages'),
            'res_model': 'whatsapp.automation.message',
            'view_mode': 'list,form',
            'domain': [('rule_id', '=', self.id)],
        }

    def action_test_rule(self):
        """Generate a test/preview message without actually sending."""
        self.ensure_one()
        engine = self.env['whatsapp.automation.rule.engine']
        result = engine.test_rule(self)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Test Result'),
                'message': result.get('message', _('Test completed.')),
                'type': 'info' if result.get('success') else 'warning',
                'sticky': False,
            },
        }

    def _get_effective_provider(self):
        """Return the provider to use: rule-specific or company default."""
        self.ensure_one()
        if self.provider_id:
            return self.provider_id
        return self.env['whatsapp.automation.provider'].search([
            ('default_provider', '=', True),
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False),
        ], limit=1)

    def _get_domain(self):
        """Parse and return domain filter safely."""
        self.ensure_one()
        try:
            return safe_eval(self.domain_filter or '[]')
        except Exception:
            _logger.warning('Invalid domain filter on rule %s', self.name)
            return []
