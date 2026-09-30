from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_open_whatsapp_wizard(self):
        """Open the WhatsApp composer wizard for this sales order / quotation."""
        self.ensure_one()
        partner = self.partner_id
        phone = getattr(partner, 'phone', False) or getattr(partner, 'mobile', False) or ''
        return {
            'name': _('Send Quotation via WhatsApp'),
            'type': 'ir.actions.act_window',
            'res_model': 'whatsapp.automation.send.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': 'sale.order',
                'default_res_id': self.id,
                'default_partner_id': partner.id if partner else False,
                'default_phone': phone,
                'default_attach_pdf': True,
            },
        }

    def action_open_whatsapp_chat(self):
        """Open WhatsApp Live Chat conversation for this quotation / sales order."""
        self.ensure_one()
        partner = self.partner_id
        phone = getattr(partner, 'mobile', False) or getattr(partner, 'phone', False) or ''
        if not phone:
            raise UserError(_('Please configure a Phone or Mobile number on the customer (%s).') % (partner.name if partner else ''))
        conv = self.env['whatsapp.automation.conversation'].get_or_create_conversation(
            phone=phone, partner_id=partner.id if partner else False
        )
        return {
            'name': _('WhatsApp Live Chat - %s') % (partner.name if partner else phone),
            'type': 'ir.actions.act_window',
            'res_model': 'whatsapp.automation.conversation',
            'view_mode': 'form',
            'res_id': conv.id,
            'target': 'current',
        }

