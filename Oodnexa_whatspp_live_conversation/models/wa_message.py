import json
import logging
from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

MESSAGE_STATES = [
    ('draft', 'Draft'),
    ('queued', 'Queued'),
    ('processing', 'Processing'),
    ('sent', 'Sent'),
    ('delivered', 'Delivered'),
    ('read', 'Read'),
    ('failed', 'Failed'),
    ('cancelled', 'Cancelled'),
]

MESSAGE_TYPES = [
    ('template', 'Template'),
    ('freeform', 'Freeform'),
    ('reminder', 'Reminder'),
    ('update', 'Update'),
    ('campaign', 'Campaign'),
    ('manual', 'Manual'),
]


class WhatsappAutomationMessage(models.Model):
    _name = 'whatsapp.automation.message'
    _description = 'WhatsApp Automation Message'
    _inherit = ['mail.thread']
    _order = 'create_date desc'
    _rec_name = 'display_name'

    name = fields.Char(
        string='Reference',
        default=lambda self: _('New'),
        readonly=True,
        copy=False,
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    provider_id = fields.Many2one(
        'whatsapp.automation.provider', string='Provider',
    )
    partner_id = fields.Many2one(
        'res.partner', string='Recipient',
    )
    phone = fields.Char(string='Phone Number')
    conversation_id = fields.Many2one(
        'whatsapp.automation.conversation',
        string='Conversation',
        index=True,
        ondelete='set null',
    )
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'wa_message_attachment_rel',
        'message_id',
        'attachment_id',
        string='Attachments',
    )
    # Polymorphic link to source document
    related_model = fields.Char(string='Related Model')
    related_res_id = fields.Integer(string='Related Record ID')
    related_record_name = fields.Char(
        compute='_compute_related_record_name',
        string='Source Document',
    )
    # Links
    rule_id = fields.Many2one('whatsapp.automation.rule', string='Rule')
    template_id = fields.Many2one('whatsapp.automation.template', string='Template')
    campaign_id = fields.Many2one('whatsapp.automation.campaign', string='Campaign')
    # State
    state = fields.Selection(
        selection=MESSAGE_STATES, default='draft',
        tracking=True, index=True,
    )
    message_type = fields.Selection(
        selection=MESSAGE_TYPES, default='template',
    )
    direction = fields.Selection(
        selection=[('outgoing', 'Outgoing'), ('incoming', 'Incoming')],
        default='outgoing',
    )
    # Content
    rendered_body = fields.Text(string='Rendered Body')
    payload_json = fields.Text(
        string='Provider Payload (JSON)',
        help='Full payload sent to the provider API.',
    )
    # Provider response
    provider_message_id = fields.Char(
        string='Provider Message ID',
        index=True,
    )
    error_message = fields.Text()
    # Timestamps
    queued_at = fields.Datetime()
    sent_at = fields.Datetime()
    delivered_at = fields.Datetime()
    read_at = fields.Datetime()
    failed_at = fields.Datetime()
    # Retry
    retry_count = fields.Integer(default=0)
    max_retries = fields.Integer(default=3)
    # Meta
    note = fields.Text()
    color = fields.Integer(compute='_compute_color')
    is_edited = fields.Boolean(default=False, string='Is Edited')
    edited_at = fields.Datetime(string='Edited At')
    is_deleted = fields.Boolean(default=False, string='Is Deleted')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'whatsapp.automation.message'
                ) or _('New')
            # Auto-link conversation if phone is available and conversation_id is not set
            if not vals.get('conversation_id') and vals.get('phone'):
                try:
                    conv = self.env['whatsapp.automation.conversation'].get_or_create_conversation(
                        phone=vals.get('phone'),
                        partner_id=vals.get('partner_id'),
                        provider_id=vals.get('provider_id'),
                    )
                    if conv:
                        vals['conversation_id'] = conv.id
                except Exception as e:
                    _logger.debug('Could not auto-link conversation for message: %s', e)
        return super().create(vals_list)


    @api.depends('related_model', 'related_res_id')
    def _compute_related_record_name(self):
        for rec in self:
            if rec.related_model and rec.related_res_id:
                try:
                    record = self.env[rec.related_model].browse(rec.related_res_id)
                    rec.related_record_name = record.display_name if record.exists() else ''
                except Exception:
                    rec.related_record_name = ''
            else:
                rec.related_record_name = ''

    @api.depends('state')
    def _compute_color(self):
        color_map = {
            'draft': 0, 'queued': 4, 'processing': 2,
            'sent': 10, 'delivered': 10, 'read': 7,
            'failed': 1, 'cancelled': 3,
        }
        for rec in self:
            rec.color = color_map.get(rec.state, 0)

    def action_cancel(self):
        for rec in self:
            if rec.state in ('draft', 'queued'):
                rec.state = 'cancelled'
                # Also cancel queue entry
                queue = self.env['whatsapp.automation.queue'].search([
                    ('message_id', '=', rec.id),
                    ('state', 'in', ('pending', 'scheduled')),
                ])
                queue.write({'state': 'cancelled'})

    def action_retry(self):
        for rec in self:
            if rec.state == 'failed' and rec.retry_count < rec.max_retries:
                rec.write({
                    'state': 'queued',
                    'error_message': False,
                })
                self.env['whatsapp.automation.queue'].create({
                    'message_id': rec.id,
                    'scheduled_for': fields.Datetime.now(),
                    'state': 'pending',
                })

    def action_open_related_record(self):
        self.ensure_one()
        if self.related_model and self.related_res_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': self.related_model,
                'res_id': self.related_res_id,
                'view_mode': 'form',
                'target': 'current',
            }
        return True

    def _log_event(self, event_type, details=''):
        """Create a log entry for this message."""
        self.env['whatsapp.automation.log'].create({
            'message_id': self.id,
            'event_type': event_type,
            'details': details,
            'company_id': self.company_id.id,
        })
