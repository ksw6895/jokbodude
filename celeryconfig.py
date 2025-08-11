# celeryconfig.py
"""
Celery configuration with Redis connection stability improvements
"""
import os
from kombu import Queue

# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

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
broker_transport_options = {
    'retry_on_timeout': True,
    'retry_on_error': [
        'redis.exceptions.ConnectionError',
        'redis.exceptions.TimeoutError',
        'redis.exceptions.BusyLoadingError'
    ],
    'max_connections': 20,
    'socket_keepalive': True,
    'socket_keepalive_options': {
        'TCP_KEEPIDLE': 1,
        'TCP_KEEPINTVL': 3,
        'TCP_KEEPCNT': 5,
    },
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
worker_max_memory_per_child = 200000  # 200MB
task_time_limit = 3600  # 1 hour
task_soft_time_limit = 3300  # 55 minutes

# Error handling
task_annotations = {
    '*': {
        'rate_limit': '10/s',
        'time_limit': 3600,
        'soft_time_limit': 3300,
    },
    'tasks.run_jokbo_analysis': {
        'rate_limit': '5/s',
        'time_limit': 7200,  # 2 hours for large analysis tasks
        'soft_time_limit': 6900,  # 1h 55min
    },
    'tasks.run_lesson_analysis': {
        'rate_limit': '5/s', 
        'time_limit': 7200,  # 2 hours for large analysis tasks
        'soft_time_limit': 6900,  # 1h 55min
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
    'socket_keepalive_options': {
        'TCP_KEEPIDLE': 1,
        'TCP_KEEPINTVL': 3, 
        'TCP_KEEPCNT': 5,
    },
    'health_check_interval': 30,
    'socket_timeout': 30.0,
    'socket_connect_timeout': 30.0,
}