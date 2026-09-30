import logging
from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

QUEUE_STATES = [
    ('pending', 'Pending'),
    ('scheduled', 'Scheduled'),
    ('processing', 'Processing'),
    ('done', 'Done'),
    ('failed', 'Failed'),
    ('cancelled', 'Cancelled'),
]


class WhatsappAutomationQueue(models.Model):
    _name = 'whatsapp.automation.queue'
    _description = 'WhatsApp Automation Message Queue'
    _order = 'scheduled_for, id'

    message_id = fields.Many2one(
        'whatsapp.automation.message',
        string='Message',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='message_id.company_id', store=True,
    )
    scheduled_for = fields.Datetime(
        string='Scheduled For',
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    state = fields.Selection(
        selection=QUEUE_STATES,
        default='pending',
        index=True,
    )
    attempts = fields.Integer(default=0)
    max_attempts = fields.Integer(default=3)
    last_attempt_at = fields.Datetime()
    next_attempt_at = fields.Datetime()
    locked_by = fields.Char(
        help='Worker identifier holding the lock.',
    )
    lock_expires_at = fields.Datetime()
    error_detail = fields.Text()

    def _acquire_batch(self, batch_size=50, worker_id='default'):
        """Acquire a batch of pending queue items for processing."""
        now = fields.Datetime.now()
        # Find pending/scheduled items that are due
        items = self.search([
            ('state', 'in', ('pending', 'scheduled')),
            ('scheduled_for', '<=', now),
            '|',
            ('locked_by', '=', False),
            ('lock_expires_at', '<', now),
        ], limit=batch_size, order='scheduled_for')

        if items:
            from datetime import timedelta
            lock_until = fields.Datetime.to_string(
                fields.Datetime.from_string(now) + timedelta(minutes=5)
            )
            items.write({
                'locked_by': worker_id,
                'lock_expires_at': lock_until,
                'state': 'processing',
            })
        return items

    def _mark_done(self):
        self.write({
            'state': 'done',
            'locked_by': False,
            'lock_expires_at': False,
            'last_attempt_at': fields.Datetime.now(),
        })

    def _mark_failed(self, error=''):
        from datetime import timedelta
        now = fields.Datetime.now()
        for item in self:
            attempts = item.attempts + 1
            vals = {
                'state': 'failed' if attempts >= item.max_attempts else 'pending',
                'attempts': attempts,
                'last_attempt_at': now,
                'locked_by': False,
                'lock_expires_at': False,
                'error_detail': error,
            }
            if attempts < item.max_attempts:
                # Exponential backoff: 2^attempts minutes
                delay_minutes = min(2 ** attempts, 60)
                vals['next_attempt_at'] = fields.Datetime.to_string(
                    fields.Datetime.from_string(now) + timedelta(minutes=delay_minutes)
                )
                vals['scheduled_for'] = vals['next_attempt_at']
            item.write(vals)
