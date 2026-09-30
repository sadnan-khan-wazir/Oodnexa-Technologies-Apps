from odoo import api, fields, models, _


class WhatsappAutomationCategory(models.Model):
    _name = 'whatsapp.automation.category'
    _description = 'WhatsApp Automation Category'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer()
    description = fields.Text(translate=True)
    parent_id = fields.Many2one('whatsapp.automation.category', string='Parent Category', ondelete='set null')
    child_ids = fields.One2many('whatsapp.automation.category', 'parent_id', string='Sub Categories')
    rule_count = fields.Integer(compute='_compute_rule_count', string='Rules')
    template_count = fields.Integer(compute='_compute_template_count', string='Templates')

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Category code must be unique.'),
    ]

    def _compute_rule_count(self):
        data = self.env['whatsapp.automation.rule']._read_group(
            [('category_id', 'in', self.ids)],
            ['category_id'],
            ['__count'],
        )
        mapped = {cat.id: count for cat, count in data}
        for rec in self:
            rec.rule_count = mapped.get(rec.id, 0)

    def _compute_template_count(self):
        data = self.env['whatsapp.automation.template']._read_group(
            [('category_id', 'in', self.ids)],
            ['category_id'],
            ['__count'],
        )
        mapped = {cat.id: count for cat, count in data}
        for rec in self:
            rec.template_count = mapped.get(rec.id, 0)
