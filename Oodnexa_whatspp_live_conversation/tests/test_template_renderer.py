"""
Tests for Template Renderer Service
====================================
Verifies variable substitution, builtin resolvers, schema-based rendering,
HTML escaping, and edge cases.
"""
from unittest.mock import MagicMock, patch
from odoo.tests.common import TransactionCase


class TestTemplateRenderer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))

        # Create a provider
        cls.provider = cls.env['whatsapp.automation.provider'].create({
            'name': 'Test Provider',
            'provider_type': 'generic',
            'api_base_url': 'https://api.example.com/v1',
            'sandbox_mode': True,
            'default_provider': True,
            'status': 'active',
        })

        # Create category
        cls.category = cls.env['whatsapp.automation.category'].create({
            'name': 'Test Category',
            'code': 'TEST',
            'color': 3,
        })

        # Create a partner
        cls.partner = cls.env['res.partner'].create({
            'name': 'John Doe',
            'email': 'john@example.com',
            'phone': '+1234567890',
            'mobile': '+1987654321',
        })

        # Create a basic template
        cls.template_basic = cls.env['whatsapp.automation.template'].create({
            'name': 'Basic Template',
            'template_type': 'text',
            'template_category': 'utility',
            'category_id': cls.category.id,
            'body_text': 'Hello {{customer_name}}, welcome to {{company_name}}!',
            'approval_status': 'approved',
        })

        # Template with header/footer
        cls.template_full = cls.env['whatsapp.automation.template'].create({
            'name': 'Full Template',
            'template_type': 'text',
            'template_category': 'utility',
            'category_id': cls.category.id,
            'header_type': 'text',
            'header_text': 'Order Update: {{order_number}}',
            'body_text': 'Hi {{customer_name}}, your order {{order_number}} total is {{order_total}}.',
            'footer_text': 'Thanks from {{company_name}}',
            'approval_status': 'approved',
        })

        # Template with schema
        cls.template_schema = cls.env['whatsapp.automation.template'].create({
            'name': 'Schema Template',
            'template_type': 'text',
            'template_category': 'utility',
            'category_id': cls.category.id,
            'body_text': 'Your {{product_name}} order is {{custom_status}}.',
            'variable_schema_json': '[{"name":"product_name","field":"name","default":"N/A"},{"name":"custom_status","field":"","default":"pending"}]',
            'approval_status': 'approved',
        })

        cls.renderer = cls.env['whatsapp.automation.template.renderer']

    # ------------------------------------------------------------------
    # Variable Substitution
    # ------------------------------------------------------------------

    def test_basic_substitution(self):
        """Test that builtin variables are correctly replaced."""
        result = self.renderer.render(self.template_basic, partner=self.partner)
        self.assertIn('John Doe', result['body'])
        self.assertIn(self.env.company.name, result['body'])
        self.assertNotIn('{{', result['body'])

    def test_full_template_render(self):
        """Test header, body, and footer rendering."""
        result = self.renderer.render(self.template_full, partner=self.partner)
        self.assertIn('header', result)
        self.assertIn('body', result)
        self.assertIn('footer', result)
        self.assertIn('John Doe', result['body'])
        self.assertIn(self.env.company.name, result['footer'])

    def test_missing_variable_gets_empty(self):
        """Variables not found in any source should render as empty."""
        template = self.env['whatsapp.automation.template'].create({
            'name': 'Unknown Var Template',
            'template_type': 'text',
            'template_category': 'utility',
            'category_id': self.category.id,
            'body_text': 'Value: {{nonexistent_variable}} end',
            'approval_status': 'approved',
        })
        result = self.renderer.render(template, partner=self.partner)
        self.assertEqual(result['body'], 'Value:  end')

    def test_extra_vars_override(self):
        """Extra variables should override builtins."""
        result = self.renderer.render(
            self.template_basic,
            partner=self.partner,
            extra_vars={'customer_name': 'Override Name'},
        )
        self.assertIn('Override Name', result['body'])
        self.assertNotIn('John Doe', result['body'])

    def test_html_escape_in_values(self):
        """Values containing HTML-like content should be escaped."""
        partner = self.env['res.partner'].create({
            'name': '<script>alert("xss")</script>',
            'mobile': '+1999999999',
        })
        result = self.renderer.render(self.template_basic, partner=partner)
        self.assertNotIn('<script>', result['body'])
        self.assertIn('&lt;script&gt;', result['body'])

    # ------------------------------------------------------------------
    # Schema Variables
    # ------------------------------------------------------------------

    def test_schema_default_values(self):
        """Schema-defined variables should use defaults when no record."""
        result = self.renderer.render(self.template_schema)
        self.assertIn('pending', result['body'])

    def test_schema_field_resolution(self):
        """Schema variables with field paths should resolve from record."""
        result = self.renderer.render(self.template_schema, record=self.partner)
        # field="name" should resolve to partner.name
        self.assertIn(self.partner.name, result['body'])

    # ------------------------------------------------------------------
    # Utility Methods
    # ------------------------------------------------------------------

    def test_render_body_only(self):
        """render_body_only should return just the body string."""
        body = self.renderer.render_body_only(self.template_basic, partner=self.partner)
        self.assertIsInstance(body, str)
        self.assertIn('John Doe', body)

    def test_preview(self):
        """Preview should use current user as fallback partner."""
        result = self.renderer.preview(self.template_basic)
        self.assertIn('header', result)
        self.assertIn('body', result)

    def test_get_available_variables(self):
        """get_available_variables should list all placeholders."""
        vars_list = self.renderer.get_available_variables(self.template_full)
        var_names = [v['name'] for v in vars_list]
        self.assertIn('customer_name', var_names)
        self.assertIn('order_number', var_names)
        self.assertIn('order_total', var_names)
        self.assertIn('company_name', var_names)

    def test_empty_template(self):
        """Empty body should render to empty string."""
        template = self.env['whatsapp.automation.template'].create({
            'name': 'Empty Template',
            'template_type': 'text',
            'template_category': 'utility',
            'category_id': self.category.id,
            'body_text': '',
            'approval_status': 'approved',
        })
        result = self.renderer.render(template, partner=self.partner)
        self.assertEqual(result['body'], '')

    def test_variable_values_in_result(self):
        """Render result should include variable_values dict."""
        result = self.renderer.render(self.template_basic, partner=self.partner)
        self.assertIn('variable_values', result)
        self.assertIsInstance(result['variable_values'], dict)
        self.assertIn('customer_name', result['variable_values'])
