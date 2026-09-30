import json
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class WhatsappAutomationTemplate(models.Model):
    _name = 'whatsapp.automation.template'
    _description = 'WhatsApp Automation Template'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    category_id = fields.Many2one(
        'whatsapp.automation.category', string='Category',
    )
    template_type = fields.Selection(
        selection=[
            ('text', 'Text Only'),
            ('template', 'Provider Template'),
            ('media', 'Media (placeholder)'),
        ],
        default='text',
        required=True,
    )
    provider_template_id = fields.Char(
        string='Provider Template ID',
        help='External template identifier registered with the WhatsApp provider.',
    )
    language_code = fields.Char(
        string='Language Code',
        default='en',
        help='BCP 47 language tag, e.g. en, en_US, es, fr.',
    )
    # Header
    header_type = fields.Selection(
        selection=[
            ('none', 'None'),
            ('text', 'Text'),
            ('image', 'Image (placeholder)'),
            ('document', 'Document (placeholder)'),
            ('video', 'Video (placeholder)'),
        ],
        default='none',
    )
    header_text = fields.Char(string='Header Text')
    # Body
    body_text = fields.Text(
        string='Body Text',
        required=True,
        help='Use {{variable_name}} for dynamic placeholders.',
    )
    # Footer
    footer_text = fields.Char(string='Footer Text')
    # Variables
    variable_schema_json = fields.Text(
        string='Variable Schema (JSON)',
        help='JSON array defining available variables and their source fields.',
        default='[]',
    )
    # Buttons
    button_config_json = fields.Text(
        string='Button Config (JSON)',
        help='JSON array defining button configuration for interactive templates.',
        default='[]',
    )
    # Meta
    template_category = fields.Selection(
        selection=[
            ('marketing', 'Marketing'),
            ('utility', 'Utility'),
            ('authentication', 'Authentication'),
        ],
        default='utility',
    )
    approval_status = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('pending', 'Pending Approval'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        default='draft',
        tracking=True,
    )
    notes = fields.Html()
    color = fields.Integer()

    # Computed
    variable_list = fields.Text(
        compute='_compute_variable_list',
        string='Detected Variables',
    )
    message_count = fields.Integer(compute='_compute_message_count')
    preview_body = fields.Text(compute='_compute_preview_body', string='Preview')

    @api.depends('body_text')
    def _compute_variable_list(self):
        import re
        for rec in self:
            if rec.body_text:
                variables = re.findall(r'\{\{(\w+)\}\}', rec.body_text)
                rec.variable_list = ', '.join(sorted(set(variables))) if variables else ''
            else:
                rec.variable_list = ''

    @api.depends('body_text', 'header_text', 'footer_text')
    def _compute_preview_body(self):
        for rec in self:
            parts = []
            if rec.header_text:
                parts.append(f'[Header] {rec.header_text}')
            if rec.body_text:
                parts.append(rec.body_text)
            if rec.footer_text:
                parts.append(f'[Footer] {rec.footer_text}')
            rec.preview_body = '\n'.join(parts)

    def _compute_message_count(self):
        data = self.env['whatsapp.automation.message']._read_group(
            [('template_id', 'in', self.ids)],
            ['template_id'],
            ['__count'],
        )
        mapped = {tmpl.id: count for tmpl, count in data}
        for rec in self:
            rec.message_count = mapped.get(rec.id, 0)

    @api.constrains('variable_schema_json')
    def _check_variable_schema(self):
        for rec in self:
            if rec.variable_schema_json:
                try:
                    parsed = json.loads(rec.variable_schema_json)
                    if not isinstance(parsed, list):
                        raise ValidationError(_('Variable schema must be a JSON array.'))
                except (json.JSONDecodeError, TypeError):
                    raise ValidationError(_('Invalid JSON in variable schema.'))

    @api.constrains('button_config_json')
    def _check_button_config(self):
        for rec in self:
            if rec.button_config_json:
                try:
                    parsed = json.loads(rec.button_config_json)
                    if not isinstance(parsed, list):
                        raise ValidationError(_('Button config must be a JSON array.'))
                except (json.JSONDecodeError, TypeError):
                    raise ValidationError(_('Invalid JSON in button config.'))

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Messages'),
            'res_model': 'whatsapp.automation.message',
            'view_mode': 'list,form',
            'domain': [('template_id', '=', self.id)],
        }

    def action_approve(self):
        self.write({'approval_status': 'approved'})

    def action_reject(self):
        self.write({'approval_status': 'rejected'})
