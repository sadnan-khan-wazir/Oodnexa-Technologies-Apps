import base64
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from ..services.provider_adapter import normalize_phone

_logger = logging.getLogger(__name__)


class WhatsappSendWizard(models.TransientModel):
    _name = 'whatsapp.automation.send.wizard'
    _description = 'WhatsApp Send Wizard'

    res_model = fields.Char(
        string='Target Model',
        required=True,
        readonly=True,
    )
    res_id = fields.Integer(
        string='Target Record ID',
        required=True,
        readonly=True,
    )
    document_name = fields.Char(
        string='Document Reference',
        compute='_compute_document_name',
        readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer / Recipient',
        required=True,
    )
    phone = fields.Char(
        string='Recipient Phone',
        required=True,
        help='Recipient phone number with country code (e.g. +971501234567 or +923001234567)',
    )
    provider_id = fields.Many2one(
        'whatsapp.automation.provider',
        string='Provider',
        domain=[('active', '=', True)],
        help='Defaults to active default Meta Cloud API provider.',
    )
    template_id = fields.Many2one(
        'whatsapp.automation.template',
        string='Template',
        required=True,
        domain=[('active', '=', True)],
    )
    has_pdf_report = fields.Boolean(
        string='Has PDF Report',
        compute='_compute_has_pdf_report',
    )
    attach_pdf = fields.Boolean(
        string='Attach PDF Document',
        default=True,
    )
    pdf_filename = fields.Char(
        string='PDF File Name',
        compute='_compute_pdf_filename',
        readonly=False,
        store=True,
    )
    portal_url = fields.Char(
        string='Portal Link',
        compute='_compute_portal_url',
        readonly=False,
        store=True,
    )
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

    def _get_target_record(self):
        """Retrieve the source record."""
        self.ensure_one()
        if self.res_model and self.res_id:
            try:
                return self.env[self.res_model].browse(self.res_id)
            except Exception:
                return False
        return False

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        res_model = res.get('res_model') or self.env.context.get('default_res_model')
        res_id = res.get('res_id') or self.env.context.get('default_res_id')

        partner = False
        if res_model and res_id:
            record = self.env[res_model].browse(res_id)
            if res_model == 'res.partner':
                partner = record
            elif hasattr(record, 'partner_id') and record.partner_id:
                partner = record.partner_id

        if partner:
            if 'partner_id' in fields_list and not res.get('partner_id'):
                res['partner_id'] = partner.id
            if 'phone' in fields_list and not res.get('phone'):
                res['phone'] = getattr(partner, 'phone', False) or getattr(partner, 'mobile', False) or ''

        if 'provider_id' in fields_list and not res.get('provider_id'):
            default_provider = self.env['whatsapp.automation.provider'].search([
                ('default_provider', '=', True),
                '|', ('company_id', '=', self.env.company.id), ('company_id', '=', False),
            ], limit=1) or self.env['whatsapp.automation.provider'].search([
                ('active', '=', True),
            ], limit=1)
            if default_provider:
                res['provider_id'] = default_provider.id

        if 'template_id' in fields_list and not res.get('template_id'):
            tmpl = False
            if res_model == 'sale.order':
                tmpl = self.env['whatsapp.automation.template'].search([
                    ('active', '=', True),
                    ('name', 'ilike', 'quotation'),
                ], limit=1)
            elif res_model == 'account.move':
                tmpl = self.env['whatsapp.automation.template'].search([
                    ('active', '=', True),
                    ('name', 'ilike', 'invoice'),
                ], limit=1)
            elif res_model == 'res.partner':
                tmpl = self.env['whatsapp.automation.template'].search([
                    ('active', '=', True),
                    ('name', 'ilike', 'portal'),
                ], limit=1)

            if not tmpl:
                tmpl = self.env['whatsapp.automation.template'].search([
                    ('active', '=', True),
                ], limit=1)
            if tmpl:
                res['template_id'] = tmpl.id

        if 'attach_pdf' in fields_list and (res_model in ('sale.order', 'account.move') or (res_model == 'tailor.order' and 'tailor.order' in self.env)):
            res['attach_pdf'] = True

        return res

    @api.depends('res_model', 'res_id')
    def _compute_document_name(self):
        for wiz in self:
            rec = wiz._get_target_record()
            wiz.document_name = rec.display_name if rec and hasattr(rec, 'display_name') else (wiz.res_model or '')

    @api.depends('res_model')
    def _compute_has_pdf_report(self):
        for wiz in self:
            wiz.has_pdf_report = wiz.res_model in ('sale.order', 'account.move') or (wiz.res_model == 'tailor.order' and 'tailor.order' in self.env)

    @api.depends('res_model', 'res_id')
    def _compute_pdf_filename(self):
        for wiz in self:
            rec = wiz._get_target_record()
            ref_name = rec.name if rec and hasattr(rec, 'name') else 'Document'
            clean_ref = str(ref_name).replace('/', '_').replace(' ', '_')
            if wiz.res_model == 'sale.order':
                wiz.pdf_filename = f"Quotation_{clean_ref}.pdf"
            elif wiz.res_model == 'account.move':
                wiz.pdf_filename = f"Invoice_{clean_ref}.pdf"
            elif wiz.res_model == 'tailor.order':
                wiz.pdf_filename = f"Tailor_Order_{clean_ref}.pdf"
            else:
                wiz.pdf_filename = f"{clean_ref}.pdf"

    @api.depends('res_model', 'res_id', 'partner_id')
    def _compute_portal_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        for wiz in self:
            rec = wiz._get_target_record()
            url = ''
            if wiz.res_model == 'sale.order' and rec and hasattr(rec, 'get_portal_url'):
                url = f"{base_url}{rec.get_portal_url()}"
            elif wiz.res_model == 'res.partner' and rec:
                url = rec._get_signup_url() if hasattr(rec, '_get_signup_url') else f"{base_url}/my"
            elif wiz.partner_id:
                url = wiz.partner_id._get_signup_url() if hasattr(wiz.partner_id, '_get_signup_url') else f"{base_url}/my"
            wiz.portal_url = url or ''

    @api.depends('template_id', 'res_model', 'res_id', 'partner_id', 'portal_url')
    def _compute_preview(self):
        renderer = self.env['whatsapp.automation.template.renderer']
        for wiz in self:
            if wiz.template_id:
                rec = wiz._get_target_record()
                extra_vars = {}
                if wiz.portal_url:
                    extra_vars['portal_url'] = wiz.portal_url

                rendered = renderer.render(
                    wiz.template_id,
                    record=rec,
                    partner=wiz.partner_id,
                    extra_vars=extra_vars,
                )
                wiz.preview_header = rendered.get('header', '')
                wiz.preview_body = rendered.get('body', '')
                wiz.preview_footer = rendered.get('footer', '')
            else:
                wiz.preview_header = ''
                wiz.preview_body = ''
                wiz.preview_footer = ''

    def _render_pdf_bytes(self):
        """Render the PDF report binary for the current record."""
        self.ensure_one()
        rec = self._get_target_record()
        if not rec or not rec.exists():
            return False, ''

        if self.res_model == 'sale.order':
            pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                'sale.action_report_saleorder', [rec.id]
            )
            return pdf_content, self.pdf_filename or f"Quotation_{rec.name}.pdf"
        elif self.res_model == 'account.move':
            pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                'account.account_invoices', [rec.id]
            )
            return pdf_content, self.pdf_filename or f"Invoice_{rec.name}.pdf"
        elif self.res_model == 'tailor.order' and 'tailor.order' in self.env:
            try:
                pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                    'tailor.tailor_order_print', [rec.id]
                )
                return pdf_content, self.pdf_filename or f"Tailor_Order_{rec.name}.pdf"
            except Exception:
                return False, ''
        return False, ''

    def action_send_whatsapp(self):
        """Send message (with PDF if selected) via Meta Cloud API."""
        self.ensure_one()

        phone = normalize_phone(self.phone)
        if not phone:
            raise UserError(_('Please provide a valid recipient phone number with country code (e.g. +971501234567).'))

        provider = self.provider_id
        if not provider:
            provider = self.env['whatsapp.automation.provider'].search([
                ('default_provider', '=', True),
                '|', ('company_id', '=', self.env.company.id), ('company_id', '=', False),
            ], limit=1) or self.env['whatsapp.automation.provider'].search([
                ('active', '=', True),
            ], limit=1)

        if not provider:
            raise UserError(_('No active WhatsApp provider found. Please configure Meta Cloud API in WhatsApp > Configuration > Providers.'))

        if not self.template_id:
            raise UserError(_('Please select a WhatsApp template.'))

        renderer = self.env['whatsapp.automation.template.renderer']
        rec = self._get_target_record()
        extra_vars = {}
        if self.portal_url:
            extra_vars['portal_url'] = self.portal_url

        rendered = renderer.render(
            self.template_id,
            record=rec,
            partner=self.partner_id,
            extra_vars=extra_vars,
        )
        body = rendered.get('body', '')

        adapter_service = self.env['whatsapp.automation.provider.adapter']
        pdf_content = False
        filename = ''

        if self.attach_pdf and self.has_pdf_report:
            try:
                pdf_content, filename = self._render_pdf_bytes()
            except Exception as e:
                _logger.warning('Failed to render PDF: %s', e)
                pdf_content = False

        if pdf_content:
            # Send document message with caption
            res = adapter_service.send_document(
                provider,
                phone=phone,
                file_content=pdf_content,
                filename=filename,
                caption=body,
                template_name=self.template_id.provider_template_id if self.template_id.template_type == 'template' else None,
                language=self.template_id.language_code or 'en',
            )
        else:
            # Send text or template message
            if self.template_id.template_type == 'template' and self.template_id.provider_template_id:
                res = adapter_service.send_message(
                    provider,
                    phone=phone,
                    template_name=self.template_id.provider_template_id,
                    language=self.template_id.language_code or 'en',
                )
            else:
                res = adapter_service.send_message(
                    provider,
                    phone=phone,
                    body=body,
                )

        if res.get('success'):
            company_id = rec.company_id.id if rec and hasattr(rec, 'company_id') and rec.company_id else self.env.company.id

            msg = self.env['whatsapp.automation.message'].create({
                'company_id': company_id,
                'provider_id': provider.id,
                'partner_id': self.partner_id.id,
                'phone': phone,
                'related_model': self.res_model,
                'related_res_id': self.res_id,
                'template_id': self.template_id.id,
                'state': 'sent',
                'message_type': 'template' if self.template_id.template_type == 'template' else 'manual',
                'rendered_body': body,
                'sent_at': fields.Datetime.now(),
                'provider_message_id': res.get('provider_message_id', ''),
            })
            msg._log_event('sent', f'Direct send to {phone}')

            # Create attachment and post note in Chatter
            attachment_ids = []
            if pdf_content:
                att = self.env['ir.attachment'].create({
                    'name': filename,
                    'type': 'binary',
                    'datas': base64.b64encode(pdf_content),
                    'res_model': self.res_model,
                    'res_id': self.res_id,
                    'mimetype': 'application/pdf',
                })
                attachment_ids.append(att.id)

            if rec and hasattr(rec, 'message_post'):
                doc_title = _("WhatsApp Document (PDF)") if pdf_content else _("WhatsApp Message")
                rec.message_post(
                    body=_('<strong>%s Sent via %s</strong> to %s:<br/><pre>%s</pre>') % (
                        doc_title, provider.name, phone, body
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                    attachment_ids=attachment_ids,
                )

            if self.res_model == 'sale.order' and rec.state == 'draft':
                rec.write({'state': 'sent'})

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
            company_id = rec.company_id.id if rec and hasattr(rec, 'company_id') and rec.company_id else self.env.company.id

            msg = self.env['whatsapp.automation.message'].create({
                'company_id': company_id,
                'provider_id': provider.id,
                'partner_id': self.partner_id.id,
                'phone': phone,
                'related_model': self.res_model,
                'related_res_id': self.res_id,
                'template_id': self.template_id.id,
                'state': 'failed',
                'error_message': error_msg,
                'message_type': 'template' if self.template_id.template_type == 'template' else 'manual',
                'rendered_body': body,
                'failed_at': fields.Datetime.now(),
            })
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
