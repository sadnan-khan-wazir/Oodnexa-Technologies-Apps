from odoo import api, fields, models, _
from odoo.exceptions import UserError



class ResPartner(models.Model):
    _inherit = 'res.partner'

    wa_opt_in = fields.Boolean(
        string='WhatsApp Opt-In',
        default=True,
        help='Contact has opted in to receive WhatsApp messages.',
    )
    wa_blacklisted = fields.Boolean(
        string='WhatsApp Blacklisted',
        default=False,
    )
    wa_message_count = fields.Integer(
        compute='_compute_wa_message_count',
        string='WhatsApp Messages',
    )
    wa_last_message_at = fields.Datetime(
        compute='_compute_wa_message_count',
        string='Last WhatsApp Message',
    )
    wa_preference_id = fields.Many2one(
        'whatsapp.automation.contact.preference',
        compute='_compute_wa_preference',
        string='WhatsApp Preference',
    )

    def _compute_wa_message_count(self):
        msg_data = self.env['whatsapp.automation.message']._read_group(
            [('partner_id', 'in', self.ids)],
            ['partner_id'],
            ['__count'],
        )
        count_map = {partner.id: count for partner, count in msg_data}

        for partner in self:
            partner.wa_message_count = count_map.get(partner.id, 0)
            if partner.wa_message_count:
                last = self.env['whatsapp.automation.message'].search([
                    ('partner_id', '=', partner.id),
                ], order='create_date desc', limit=1)
                partner.wa_last_message_at = last.create_date if last else False
            else:
                partner.wa_last_message_at = False

    def _compute_wa_preference(self):
        prefs = self.env['whatsapp.automation.contact.preference'].search([
            ('partner_id', 'in', self.ids),
        ])
        pref_map = {p.partner_id.id: p for p in prefs}
        for partner in self:
            partner.wa_preference_id = pref_map.get(partner.id)

    def action_view_wa_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('WhatsApp Messages'),
            'res_model': 'whatsapp.automation.message',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

    def _is_wa_eligible(self):
        """Check if partner can receive WhatsApp messages."""
        self.ensure_one()
        if self.wa_blacklisted:
            return False
        if not self.wa_opt_in:
            return False
        if not (getattr(self, 'phone', False) or getattr(self, 'mobile', False)):
            return False
        return True

    def action_open_whatsapp_portal_wizard(self):
        """Open WhatsApp wizard to send customer portal access link."""
        self.ensure_one()
        phone = getattr(self, 'phone', False) or getattr(self, 'mobile', False) or ''
        return {
            'name': _('Send Portal Link via WhatsApp'),
            'type': 'ir.actions.act_window',
            'res_model': 'whatsapp.automation.send.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': 'res.partner',
                'default_res_id': self.id,
                'default_partner_id': self.id,
                'default_phone': phone,
                'default_attach_pdf': False,
            },
        }

    def action_open_whatsapp_chat(self):
        """Open WhatsApp Live Chat conversation for this partner."""
        self.ensure_one()
        phone = getattr(self, 'mobile', False) or getattr(self, 'phone', False) or ''
        if not phone:
            raise UserError(_('Please configure a Phone or Mobile number for %s.') % self.name)
        conv = self.env['whatsapp.automation.conversation'].get_or_create_conversation(
            phone=phone, partner_id=self.id
        )
        return {
            'name': _('WhatsApp Live Chat - %s') % (self.name or phone),
            'type': 'ir.actions.act_window',
            'res_model': 'whatsapp.automation.conversation',
            'view_mode': 'form',
            'res_id': conv.id,
            'target': 'current',
        }

