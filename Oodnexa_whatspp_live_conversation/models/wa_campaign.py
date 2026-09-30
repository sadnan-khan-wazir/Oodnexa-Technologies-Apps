from odoo import api, fields, models, _

CAMPAIGN_STATES = [
    ('draft', 'Draft'),
    ('running', 'Running'),
    ('paused', 'Paused'),
    ('completed', 'Completed'),
    ('archived', 'Archived'),
]


class WhatsappAutomationCampaign(models.Model):
    _name = 'whatsapp.automation.campaign'
    _description = 'WhatsApp Automation Campaign'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    description = fields.Text()
    state = fields.Selection(
        selection=CAMPAIGN_STATES, default='draft',
        tracking=True,
    )
    category_id = fields.Many2one(
        'whatsapp.automation.category', string='Category',
    )
    start_date = fields.Date()
    end_date = fields.Date()
    rule_ids = fields.One2many(
        'whatsapp.automation.rule', 'campaign_id',
        string='Automation Rules',
    )
    rule_count = fields.Integer(compute='_compute_counts')
    message_count = fields.Integer(compute='_compute_counts')
    success_count = fields.Integer(compute='_compute_counts')
    failure_count = fields.Integer(compute='_compute_counts')
    notes = fields.Html()
    color = fields.Integer()

    def _compute_counts(self):
        for rec in self:
            rec.rule_count = len(rec.rule_ids)
            messages = self.env['whatsapp.automation.message'].search([
                ('campaign_id', '=', rec.id),
            ])
            rec.message_count = len(messages)
            rec.success_count = len(messages.filtered(
                lambda m: m.state in ('sent', 'delivered', 'read')
            ))
            rec.failure_count = len(messages.filtered(
                lambda m: m.state == 'failed'
            ))

    def action_start(self):
        for rec in self:
            rec.state = 'running'
            rec.rule_ids.filtered(lambda r: r.state == 'draft').action_activate()

    def action_pause(self):
        for rec in self:
            rec.state = 'paused'
            rec.rule_ids.filtered(lambda r: r.state == 'active').action_pause()

    def action_complete(self):
        self.write({'state': 'completed'})

    def action_view_rules(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Campaign Rules'),
            'res_model': 'whatsapp.automation.rule',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)],
            'context': {'default_campaign_id': self.id},
        }

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Campaign Messages'),
            'res_model': 'whatsapp.automation.message',
            'view_mode': 'list,form',
            'domain': [('campaign_id', '=', self.id)],
        }
