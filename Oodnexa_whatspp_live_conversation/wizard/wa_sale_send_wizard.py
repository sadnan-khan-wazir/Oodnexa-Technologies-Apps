import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from ..services.provider_adapter import normalize_phone

_logger = logging.getLogger(__name__)


class WhatsappSaleSendWizard(models.TransientModel):
    _name = 'whatsapp.automation.sale.send.wizard'
    _description = 'Sale Order WhatsApp Composer Wizard'

    order_id = fields.Many2one(
        'sale.order',
        string='Sale Order / Quotation',
        required=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        related='order_id.partner_id',
        readonly=True,
    )
    phone = fields.Char(
        string='Phone Number',
        required=True,
        help='Recipient phone number with country code (e.g. +971501234567)',
    )
    provider_id = fields.Many2one(
        'whatsapp.automation.provider',
        string='Provider',
        domain=[('active', '=', True)],
        help='WhatsApp service provider to use for sending. Defaults to active company default.',
    )
    template_id = fields.Many2one(
        'whatsapp.automation.template',
        string='Template',
        required=True,
        domain=[('active', '=', True)],
    )
    send_mode = fields.Selection([
        ('direct', 'Send Immediately (via API)'),
        ('queue', 'Add to Background Queue'),
    ], default='direct', string='Send Mode', required=True)

    preview_header = fields.Char(
        string='Header Preview',
        compute='_compute_preview',
    )
    preview_body = fields.Text(
        string='Message Preview',
        compute='_compute_preview',
    )
    preview_footer = fields.Char(
        string='Footer Preview',
        compute='_compute_preview',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order_id = res.get('order_id') or self.env.context.get('default_order_id')
        if order_id:
            order = self.env['sale.order'].browse(order_id)
            if 'phone' in fields_list and not res.get('phone'):
                partner = order.partner_id
                res['phone'] = getattr(partner, 'phone', False) or getattr(partner, 'mobile', False) or ''

        if 'provider_id' in fields_list and not res.get('provider_id'):
            default_provider = self.env['whatsapp.automation.provider'].search([
                ('default_provider', '=', True),
                '|', ('company_id', '=', self.env.company.id), ('company_id', '=', False),
            ], limit=1)
            if default_provider:
                res['provider_id'] = default_provider.id

        if 'template_id' in fields_list and not res.get('template_id'):
            tmpl = self.env['whatsapp.automation.template'].search([
                ('active', '=', True),
                ('category_id.code', '=', 'sales'),
            ], limit=1) or self.env['whatsapp.automation.template'].search([
                ('active', '=', True),
            ], limit=1)
            if tmpl:
                res['template_id'] = tmpl.id

        return res

    @api.depends('template_id', 'order_id')
    def _compute_preview(self):
        renderer = self.env['whatsapp.automation.template.renderer']
        for wiz in self:
            if wiz.template_id and wiz.order_id:
                rendered = renderer.render(
                    wiz.template_id,
                    record=wiz.order_id,
                    partner=wiz.order_id.partner_id,
                )
                wiz.preview_header = rendered.get('header', '')
                wiz.preview_body = rendered.get('body', '')
                wiz.preview_footer = rendered.get('footer', '')
            else:
                wiz.preview_header = ''
                wiz.preview_body = ''
                wiz.preview_footer = ''

    def action_send_whatsapp(self):
        """Send or queue the WhatsApp message for the sales order."""
        self.ensure_one()

        phone = normalize_phone(self.phone)
        if not phone:
            raise UserError(_('Please provide a valid phone number with country code (e.g. +971501234567).'))

        provider = self.provider_id
        if not provider:
            provider = self.env['whatsapp.automation.provider'].search([
                ('default_provider', '=', True),
                '|', ('company_id', '=', self.env.company.id), ('company_id', '=', False),
            ], limit=1)

        if not provider:
            raise UserError(_('No active WhatsApp provider found. Please configure one in WhatsApp Automation > Providers.'))

        template = self.template_id
        if not template:
            raise UserError(_('Please select a WhatsApp template.'))

        renderer = self.env['whatsapp.automation.template.renderer']
        rendered = renderer.render(
            template,
            record=self.order_id,
            partner=self.order_id.partner_id,
        )
        body = rendered.get('body', '')

        if self.send_mode == 'direct':
            adapter_service = self.env['whatsapp.automation.provider.adapter']
            if template.template_type == 'template' and template.provider_template_id:
                # Meta template message
                res = adapter_service.send_message(
                    provider,
                    phone=phone,
                    template_name=template.provider_template_id,
                    language=template.language_code or 'en',
                )
            else:
                # Text message
                res = adapter_service.send_message(
                    provider,
                    phone=phone,
                    body=body,
                )

            # Link or create live conversation
            ConvModel = self.env['whatsapp.automation.conversation'].sudo()
            conv = ConvModel.get_or_create_conversation(
                phone=phone,
                partner_id=self.partner_id.id if self.partner_id else False,
                provider_id=provider.id,
            )

            # Generate quotation PDF report
            att = None
            try:
                report_action = self.env.ref('sale.action_report_saleorder', raise_if_not_found=False)
                if report_action:
                    pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(report_action.id, [self.order_id.id])
                    if pdf_content:
                        att = self.env['ir.attachment'].sudo().create({
                            'name': f"{self.order_id.name.replace('/', '_')}.pdf",
                            'raw': pdf_content,
                            'mimetype': 'application/pdf',
                            'res_model': 'whatsapp.automation.message',
                            'public': True,
                        })
            except Exception as e:
                _logger.warning('Failed to generate quotation PDF: %s', e)

            if res.get('success'):
                msg_vals = {
                    'company_id': self.order_id.company_id.id,
                    'provider_id': provider.id,
                    'conversation_id': conv.id if conv else False,
                    'partner_id': self.partner_id.id,
                    'phone': phone,
                    'related_model': 'sale.order',
                    'related_res_id': self.order_id.id,
                    'template_id': template.id,
                    'state': 'sent',
                    'message_type': 'template' if template.template_type == 'template' else 'manual',
                    'rendered_body': body,
                    'sent_at': fields.Datetime.now(),
                    'provider_message_id': res.get('provider_message_id', ''),
                }
                if att:
                    msg_vals['attachment_ids'] = [(4, att.id)]
                msg = self.env['whatsapp.automation.message'].create(msg_vals)
                if att:
                    att.write({'res_id': msg.id})
                msg._log_event('sent', f'Direct send to {phone}')

                if conv:
                    conv.write({
                        'last_message_at': fields.Datetime.now(),
                        'last_message_text': body,
                        'last_message_direction': 'outgoing',
                    })

                # Post in chatter
                self.order_id.message_post(
                    body=_('<strong>WhatsApp Message Sent (API)</strong> to %s via %s:<br/><pre>%s</pre>') % (
                        phone, provider.name, body
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                    attachment_ids=[att.id] if att else None,
                )

                if self.order_id.state == 'draft':
                    self.order_id.write({'state': 'sent'})

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('WhatsApp Sent'),
                        'message': _('Message successfully sent to %s via %s.', phone, provider.name),
                        'type': 'success',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    },
                }
            else:
                error_msg = res.get('error', _('Unknown error from provider.'))
                msg_vals = {
                    'company_id': self.order_id.company_id.id,
                    'provider_id': provider.id,
                    'conversation_id': conv.id if conv else False,
                    'partner_id': self.partner_id.id,
                    'phone': phone,
                    'related_model': 'sale.order',
                    'related_res_id': self.order_id.id,
                    'template_id': template.id,
                    'state': 'failed',
                    'error_message': error_msg,
                    'message_type': 'template' if template.template_type == 'template' else 'manual',
                    'rendered_body': body,
                    'failed_at': fields.Datetime.now(),
                }
                if att:
                    msg_vals['attachment_ids'] = [(4, att.id)]
                msg = self.env['whatsapp.automation.message'].create(msg_vals)
                if att:
                    att.write({'res_id': msg.id})
                msg._log_event('failed', error_msg)

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Failed to Send WhatsApp'),
                        'message': error_msg,
                        'type': 'danger',
                        'sticky': True,
                    },
                }

        else:
            # Queue mode
            msg = self.env['whatsapp.automation.message'].create({
                'company_id': self.order_id.company_id.id,
                'provider_id': provider.id,
                'partner_id': self.partner_id.id,
                'phone': phone,
                'related_model': 'sale.order',
                'related_res_id': self.order_id.id,
                'template_id': template.id,
                'state': 'queued',
                'message_type': 'manual',
                'rendered_body': body,
                'queued_at': fields.Datetime.now(),
            })
            self.env['whatsapp.automation.queue'].create({
                'message_id': msg.id,
                'scheduled_for': fields.Datetime.now(),
                'state': 'pending',
            })
            msg._log_event('queued', 'Manual send from Sale Order')

            self.order_id.message_post(
                body=_('<strong>WhatsApp Message Queued (API)</strong> for %s:<br/><pre>%s</pre>') % (phone, body),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('WhatsApp Message Queued'),
                    'message': _('Message queued for delivery to %s.', phone),
                    'type': 'info',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                },
            }
