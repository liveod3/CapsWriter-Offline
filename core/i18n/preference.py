"""Follow the local client preference without executing edited configuration."""

import copy
from types import SimpleNamespace


def client_language_reloader(path, client_module, report):
    """Reuse full candidate validation, publishing only a detached UI setting."""
    from config_templates import config_client_template
    from core.config_reload import ConfigReloader

    snapshot = SimpleNamespace(**{
        name: SimpleNamespace(**{
            key: copy.deepcopy(value)
            for key, value in vars(getattr(client_module, name)).items()
            if not key.startswith('_')
        })
        for name, cls in vars(config_client_template).items()
        if isinstance(cls, type) and cls.__module__ == config_client_template.__name__
        and hasattr(client_module, name)
    })

    def report_rejection(message):
        # Other client settings are validated but never applied by the server.
        # Their restart/pending notifications belong to the client process.
        if message.message_id == 'config.rejected':
            from core.i18n import Notice
            report(Notice('language.client_rejected', reason=message.values['reason']))

    return ConfigReloader(
        path, snapshot, config_client_template, 'ClientConfig', {'ui_language'},
        report_rejection,
    )
