# celeryconfig.py
"""
Celery configuration with Redis connection stability improvements
"""
import os
import socket
from kombu import Queue
from settings import settings as app_settings

# Redis configuration
REDIS_URL = app_settings.REDIS_URL

# Broker settings - Redis with connection stability
broker_url = REDIS_URL
result_backend = REDIS_URL

# Fix for CPendingDeprecationWarning
broker_connection_retry_on_startup = True

# Connection pooling configuration for better stability
broker_connection_retry = True
broker_connection_retry_on_startup = True
broker_connection_max_retries = 10

# Redis connection pool settings
# Build cross-platform TCP keepalive socket options using integer constants
# redis-py expects numeric socket option keys, not string names.
KEEPALIVE_OPTS = {}
if hasattr(socket, "TCP_KEEPIDLE"):
    KEEPALIVE_OPTS[socket.TCP_KEEPIDLE] = 1
if hasattr(socket, "TCP_KEEPINTVL"):
    KEEPALIVE_OPTS[socket.TCP_KEEPINTVL] = 3
if hasattr(socket, "TCP_KEEPCNT"):
    KEEPALIVE_OPTS[socket.TCP_KEEPCNT] = 5
# macOS/BSD fallback
if not KEEPALIVE_OPTS and hasattr(socket, "TCP_KEEPALIVE"):
    KEEPALIVE_OPTS[socket.TCP_KEEPALIVE] = 60

broker_transport_options = {
    'retry_on_timeout': True,
    'retry_on_error': [
        'redis.exceptions.ConnectionError',
        'redis.exceptions.TimeoutError',
        'redis.exceptions.BusyLoadingError'
    ],
    'max_connections': 20,
    'socket_keepalive': True,
    'socket_keepalive_options': KEEPALIVE_OPTS,
    'health_check_interval': 30,
    'retry_policy': {
        'timeout': 5.0,
    }
}

# Result backend connection pool settings
result_backend_transport_options = broker_transport_options.copy()

# Worker configuration for connection loss handling
worker_cancel_long_running_tasks_on_connection_loss = True
worker_disable_rate_limits = True
worker_prefetch_multiplier = 1
worker_max_tasks_per_child = 1000
worker_acks_late = True

# Task configuration
task_acks_late = True
task_reject_on_worker_lost = True
task_serializer = 'json'
result_serializer = 'json'
accept_content = ['json']
result_expires = 7200  # 2 hours

# Timezone configuration
timezone = 'UTC'
enable_utc = True

# Task routing
task_default_queue = 'default'
task_queues = (
    Queue('default', routing_key='default'),
    Queue('analysis', routing_key='analysis'),
)

task_routes = {
    'tasks.run_jokbo_analysis': {'queue': 'analysis'},
    'tasks.run_lesson_analysis': {'queue': 'analysis'},
    'tasks.batch_analyze_single': {'queue': 'analysis'},
    'tasks.aggregate_batch': {'queue': 'analysis'},
}

# Logging configuration
worker_log_format = '[%(asctime)s: %(levelname)s/%(processName)s] %(message)s'
worker_task_log_format = '[%(asctime)s: %(levelname)s/%(processName)s][%(task_name)s(%(task_id)s)] %(message)s'

# Connection retry configuration with exponential backoff
broker_connection_retry = True
broker_connection_max_retries = None  # Retry indefinitely
broker_heartbeat = 30
broker_heartbeat_checkrate = 2.0

# Redis specific settings for connection stability
redis_max_connections = 20
redis_retry_on_timeout = True
redis_socket_timeout = 30.0
redis_socket_connect_timeout = 30.0

# Additional stability settings
worker_send_task_events = True
task_send_sent_event = True
worker_pool_restarts = True

# Beat configuration (if using periodic tasks)
beat_schedule = {}

# Security settings
worker_hijack_root_logger = False
worker_log_color = False

# Memory and performance settings
# Increase per-child memory cap to reduce OOM-related worker loss while still reclaiming memory periodically
worker_max_memory_per_child = 350000  # 350MB
# Extend task time limits to support very long analyses (from settings)
# 24 hours = 86400 seconds (set hard slightly above soft)
task_time_limit = int(app_settings.CELERY_TIME_LIMIT)
task_soft_time_limit = int(app_settings.CELERY_SOFT_TIME_LIMIT)

# Error handling
task_annotations = {
    '*': {
        'rate_limit': '10/s',
        'time_limit': int(app_settings.CELERY_TIME_LIMIT),
        'soft_time_limit': int(app_settings.CELERY_SOFT_TIME_LIMIT),
    },
    'tasks.run_jokbo_analysis': {
        'rate_limit': '5/s',
        'time_limit': int(app_settings.CELERY_TIME_LIMIT),
        'soft_time_limit': int(app_settings.CELERY_SOFT_TIME_LIMIT),
    },
    'tasks.run_lesson_analysis': {
        'rate_limit': '5/s',
        'time_limit': int(app_settings.CELERY_TIME_LIMIT),
        'soft_time_limit': int(app_settings.CELERY_SOFT_TIME_LIMIT),
    },
    'tasks.batch_analyze_single': {
        'rate_limit': '10/s',
        'time_limit': int(app_settings.CELERY_TIME_LIMIT),
        'soft_time_limit': int(app_settings.CELERY_SOFT_TIME_LIMIT),
    },
    'tasks.aggregate_batch': {
        'rate_limit': '10/s',
        'time_limit': 3600,
        'soft_time_limit': 3600,
    }
}

# Monitoring and health checks
worker_send_task_events = True
task_send_sent_event = True
worker_enable_remote_control = True

# Connection pool configuration for Redis backend
result_backend_transport_options = {
    'master_name': None,
    'retry_on_timeout': True,
    'retry_on_error': [
        'redis.exceptions.ConnectionError',
        'redis.exceptions.TimeoutError', 
        'redis.exceptions.BusyLoadingError'
    ],
    'max_connections': 20,
    'socket_keepalive': True,
    'socket_keepalive_options': KEEPALIVE_OPTS,
    'health_check_interval': 30,
    'socket_timeout': 30.0,
    'socket_connect_timeout': 30.0,
}
