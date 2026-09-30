from odoo import api, fields, models, _
from odoo.exceptions import UserError


class WhatsappBulkSendWizard(models.TransientModel):
    _name = 'whatsapp.automation.bulk.send.wizard'
    _description = 'WhatsApp Bulk Send Wizard'

    template_id = fields.Many2one(
        'whatsapp.automation.template',
        string='Template',
        required=True,
        domain=[('active', '=', True)],
    )
    provider_id = fields.Many2one(
        'whatsapp.automation.provider',
        string='Provider',
        help='Leave empty to use default provider.',
    )
    partner_ids = fields.Many2many(
        'res.partner', string='Recipients',
    )
    model_name = fields.Char(string='Source Model')
    record_ids = fields.Char(
        string='Record IDs',
        help='Comma-separated IDs of source records.',
    )
    phone_field = fields.Char(
        string='Phone Field', default='mobile',
    )
    preview_body = fields.Text(
        compute='_compute_preview', string='Preview',
    )
    recipient_count = fields.Integer(
        compute='_compute_recipient_count',
    )

    @api.depends('template_id')
    def _compute_preview(self):
        renderer = self.env['whatsapp.automation.template.renderer']
        for wiz in self:
            if wiz.template_id:
                result = renderer.preview(wiz.template_id)
                wiz.preview_body = result.get('body', '')
            else:
                wiz.preview_body = ''

    @api.depends('partner_ids')
    def _compute_recipient_count(self):
        for wiz in self:
            wiz.recipient_count = len(wiz.partner_ids)

    def action_send(self):
        """Create queued messages for all selected recipients."""
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(_('Please select at least one recipient.'))

        from ..services.provider_adapter import normalize_phone

        renderer = self.env['whatsapp.automation.template.renderer']
        queue_model = self.env['whatsapp.automation.queue']
        msg_model = self.env['whatsapp.automation.message']

        provider = self.provider_id
        if not provider:
            provider = self.env['whatsapp.automation.provider'].search([
                ('default_provider', '=', True),
                '|', ('company_id', '=', self.env.company.id), ('company_id', '=', False),
            ], limit=1)

        created = 0
        skipped = 0

        for partner in self.partner_ids:
            phone = normalize_phone(getattr(partner, 'phone', False) or getattr(partner, 'mobile', False) or '')
            if not phone:
                skipped += 1
                continue
            if partner.wa_blacklisted or not partner.wa_opt_in:
                skipped += 1
                continue

            rendered = renderer.render(
                self.template_id,
                partner=partner,
            )

            message = msg_model.create({
                'company_id': self.env.company.id,
                'provider_id': provider.id if provider else False,
                'partner_id': partner.id,
                'phone': phone,
                'template_id': self.template_id.id,
                'state': 'queued',
                'message_type': 'manual',
                'rendered_body': rendered.get('body', ''),
                'queued_at': fields.Datetime.now(),
            })

            queue_model.create({
                'message_id': message.id,
                'scheduled_for': fields.Datetime.now(),
                'state': 'pending',
            })
            message._log_event('queued', 'Bulk send wizard')
            created += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bulk Send'),
                'message': _('%d messages queued, %d skipped.', created, skipped),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
