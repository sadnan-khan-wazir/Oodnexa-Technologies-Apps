from odoo import api, fields, models, _

LOG_EVENT_TYPES = [
    ('created', 'Created'),
    ('queued', 'Queued'),
    ('processing', 'Processing'),
    ('sent', 'Sent'),
    ('delivered', 'Delivered'),
    ('read', 'Read'),
    ('failed', 'Failed'),
    ('retried', 'Retried'),
    ('cancelled', 'Cancelled'),
    ('updated', 'Updated'),
    ('deleted', 'Deleted'),
    ('status_update', 'Status Update'),
    ('error', 'Error'),
]


class WhatsappAutomationLog(models.Model):
    _name = 'whatsapp.automation.log'
    _description = 'WhatsApp Automation Log'
    _order = 'create_date desc'

    message_id = fields.Many2one(
        'whatsapp.automation.message',
        string='Message',
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    event_type = fields.Selection(
        selection=LOG_EVENT_TYPES,
        required=True,
        index=True,
    )
    details = fields.Text()
    provider_id = fields.Many2one(
        related='message_id.provider_id', store=True,
        string='Provider',
    )
    rule_id = fields.Many2one(
        related='message_id.rule_id', store=True,
        string='Rule',
    )
    template_id = fields.Many2one(
        related='message_id.template_id', store=True,
        string='Template',
    )
    partner_id = fields.Many2one(
        related='message_id.partner_id', store=True,
        string='Partner',
    )
    phone = fields.Char(related='message_id.phone', store=True)
    message_state = fields.Selection(
        related='message_id.state', store=True,
        string='Message State',
    )
