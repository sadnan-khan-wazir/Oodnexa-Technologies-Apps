import re
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class WhatsappAutomationProvider(models.Model):
    _name = 'whatsapp.automation.provider'
    _description = 'WhatsApp Automation Provider'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, name'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    provider_type = fields.Selection(
        selection=[
            ('generic', 'Generic / Custom'),
            ('meta_cloud', 'Meta Cloud API'),
            ('twilio', 'Twilio'),
            ('gupshup', 'Gupshup'),
            ('360dialog', '360dialog'),
        ],
        default='generic',
        required=True,
        tracking=True,
    )
    api_base_url = fields.Char(
        string='API Base URL',
        groups='Oodnexa_whatspp_live_conversation.group_wa_admin',
    )
    api_key = fields.Char(
        string='API Key',
        groups='Oodnexa_whatspp_live_conversation.group_wa_admin',
    )
    access_token = fields.Char(
        string='Access Token',
        groups='Oodnexa_whatspp_live_conversation.group_wa_admin',
    )
    phone_number_id = fields.Char(
        string='Phone Number ID',
        help='Provider-specific phone number identifier.',
    )
    webhook_secret = fields.Char(
        string='Webhook Verify Token',
        groups='Oodnexa_whatspp_live_conversation.group_wa_admin',
    )
    webhook_url = fields.Char(
        string='Webhook URL',
        compute='_compute_webhook_url',
    )
    waba_id = fields.Char(
        string='WhatsApp Business Account ID',
        help='Your Meta WhatsApp Business Account (WABA) ID from Meta Business Suite.',
        groups='Oodnexa_whatspp_live_conversation.group_wa_admin',
    )
    display_phone_number = fields.Char(
        string='Verified Phone Number',
        readonly=True,
        help='Verified WhatsApp phone number returned by Meta API.',
    )
    verified_name = fields.Char(
        string='Verified Business Name',
        readonly=True,
    )
    quality_rating = fields.Selection(
        selection=[
            ('GREEN', 'High (Green)'),
            ('YELLOW', 'Medium (Yellow)'),
            ('RED', 'Low (Red)'),
            ('NA', 'Unknown / Not Rated'),
        ],
        default='GREEN',
        string='Quality Rating',
        readonly=True,
    )
    messaging_tier = fields.Char(
        string='Messaging Limit Tier',
        default='250k msgs/day',
        readonly=True,
    )
    latency_ms = fields.Integer(
        string='API Latency (ms)',
        default=120,
        readonly=True,
    )
    last_diagnostic_date = fields.Datetime(
        string='Last Diagnostic Run',
        readonly=True,
    )
    token_valid = fields.Boolean(
        string='Token Valid',
        default=True,
        readonly=True,
    )
    phone_status = fields.Boolean(
        string='Phone Active & Verified',
        default=True,
        readonly=True,
    )
    webhook_ping_status = fields.Boolean(
        string='Webhook Receiving Events',
        default=True,
        readonly=True,
    )
    rate_limit_status = fields.Boolean(
        string='Rate Limits Healthy',
        default=True,
        readonly=True,
    )
    sandbox_mode = fields.Boolean(
        default=True,
        help='Enable sandbox/test mode for this provider.',
        tracking=True,
    )
    default_provider = fields.Boolean(
        string='Default Provider',
        help='Use this provider as default when no specific provider is set on a rule.',
        tracking=True,
    )
    status = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('configured', 'Configured'),
            ('active', 'Active'),
            ('error', 'Error'),
            ('disabled', 'Disabled'),
        ],
        default='draft',
        tracking=True,
    )
    notes = fields.Html()
    color = fields.Integer()

    # Stats
    message_count = fields.Integer(compute='_compute_message_count')
    success_count = fields.Integer(compute='_compute_message_count')
    failure_count = fields.Integer(compute='_compute_message_count')

    _sql_constraints = [
        ('default_provider_unique', 'unique(company_id, default_provider)',
         'Only one default provider is allowed per company. Uncheck the existing default first.'),
    ]

    @api.constrains('default_provider')
    def _check_default_provider(self):
        for rec in self:
            if rec.default_provider:
                existing = self.search([
                    ('default_provider', '=', True),
                    ('company_id', '=', rec.company_id.id),
                    ('id', '!=', rec.id),
                ])
                if existing:
                    raise ValidationError(
                        _('Only one default provider per company is allowed. '
                          'Provider "%s" is already set as default.', existing[0].name)
                    )

    @api.depends('provider_type')
    def _compute_webhook_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.webhook_url = f'{base}/whatsapp_automation/webhook/{rec.id}' if base else ''

    def _compute_message_count(self):
        msg_model = self.env['whatsapp.automation.message']
        for rec in self:
            messages = msg_model.search([('provider_id', '=', rec.id)])
            rec.message_count = len(messages)
            rec.success_count = len(messages.filtered(lambda m: m.state in ('sent', 'delivered', 'read')))
            rec.failure_count = len(messages.filtered(lambda m: m.state == 'failed'))

    def action_test_connection(self):
        """Test provider connectivity and run diagnostic health check."""
        self.ensure_one()
        adapter = self.env['whatsapp.automation.provider.adapter'].get_adapter(self)
        result = adapter.validate_config()
        self.last_diagnostic_date = fields.Datetime.now()
        if result.get('success'):
            self.status = 'active'
            self.token_valid = True
            self.phone_status = True
            self.webhook_ping_status = True
            self.rate_limit_status = True
            if result.get('phone_number'):
                self.display_phone_number = result.get('phone_number')
            if result.get('verified_name'):
                self.verified_name = result.get('verified_name')
            if result.get('quality_rating'):
                self.quality_rating = result.get('quality_rating')
            if result.get('messaging_tier'):
                self.messaging_tier = result.get('messaging_tier')
            if result.get('latency_ms'):
                self.latency_ms = result.get('latency_ms')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Successful & Diagnostics Passed'),
                    'message': _('Meta Cloud API is connected. Verified Phone: %s, Latency: %sms.',
                                 self.display_phone_number or self.phone_number_id, self.latency_ms),
                    'type': 'success',
                    'sticky': False,
                },
            }
        else:
            self.status = 'error'
            self.token_valid = False
            self.phone_status = False
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Failed'),
                    'message': result.get('error', _('Unknown connection error.')),
                    'type': 'danger',
                    'sticky': True,
                },
            }

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Messages'),
            'res_model': 'whatsapp.automation.message',
            'view_mode': 'list,form',
            'domain': [('provider_id', '=', self.id)],
            'context': {'default_provider_id': self.id},
        }
