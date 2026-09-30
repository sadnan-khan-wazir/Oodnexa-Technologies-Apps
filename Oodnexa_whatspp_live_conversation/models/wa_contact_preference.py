from odoo import api, fields, models, _


class WhatsappAutomationContactPreference(models.Model):
    _name = 'whatsapp.automation.contact.preference'
    _description = 'WhatsApp Automation Contact Preference'
    _rec_name = 'partner_id'
    _sql_constraints = [
        ('partner_unique', 'unique(partner_id)', 'Preference record must be unique per partner.'),
    ]

    partner_id = fields.Many2one(
        'res.partner', string='Contact',
        required=True, ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    opt_in = fields.Boolean(
        string='Opted In',
        default=True,
        help='Contact has opted in to receive WhatsApp messages.',
    )
    opt_in_date = fields.Datetime(string='Opt-In Date')
    opt_out_date = fields.Datetime(string='Opt-Out Date')
    opt_out_reason = fields.Char()
    preferred_language = fields.Char(
        string='Preferred Language',
        default='en',
    )
    blacklisted = fields.Boolean(
        default=False,
        help='Block all WhatsApp messages to this contact.',
    )
    last_message_at = fields.Datetime(string='Last Message Sent')
    total_messages_sent = fields.Integer(default=0)

    def action_opt_in(self):
        self.write({
            'opt_in': True,
            'opt_in_date': fields.Datetime.now(),
            'opt_out_date': False,
            'opt_out_reason': False,
            'blacklisted': False,
        })

    def action_opt_out(self):
        self.write({
            'opt_in': False,
            'opt_out_date': fields.Datetime.now(),
        })

    def action_blacklist(self):
        self.write({
            'blacklisted': True,
            'opt_in': False,
            'opt_out_date': fields.Datetime.now(),
            'opt_out_reason': 'Blacklisted by admin',
        })
