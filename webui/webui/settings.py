import configparser

"""
Django settings for webui project.

Generated by 'django-admin startproject' using Django 2.1.3.

For more information on this file, see
https://docs.djangoproject.com/en/2.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/2.1/ref/settings/
"""

import os
import logging.config

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/2.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '%ma*-g0xjs1clg9u3k21mi4av%&j5-4sqn&)&!+owze@+9_es)'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# This configuration file contains admin credentials for each CSP
CONFIG_FILE = '/etc/pcw.ini'

ALLOWED_HOSTS = ['127.0.0.1', 'publiccloud.qa.suse.de']


# Application definition

INSTALLED_APPS = [
    'ocw.apps.OcwConfig',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_tables2',
    'django_filters',
    'bootstrap3',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'webui.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'webui.wsgi.application'


# Database
# https://docs.djangoproject.com/en/2.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
    }
}


# Password validation
# https://docs.djangoproject.com/en/2.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.'
                + 'UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.'
                + 'MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.'
                + 'CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.'
                + 'NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/2.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/2.1/howto/static-files/
STATIC_ROOT = os.path.join(BASE_DIR, "nginx/static/")

STATIC_URL = '/static/'

logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
    },
    'loggers': {
        '': {
            'level': 'WARNING',
            'handlers': ['console'],
        },
        'ocw': {
            'level': 'DEBUG',
            'handlers': ['console'],
            'propagate': False,
        }
    },
})


class ConfigFile:
    __instance = None
    __file_mtime = None
    filename = None
    config = None

    def __new__(cls, filename=CONFIG_FILE):
        if ConfigFile.__instance is None:
            ConfigFile.__instance = object.__new__(cls)
        ConfigFile.__instance.filename = filename
        return ConfigFile.__instance

    def check_file(self):
        if self.__file_mtime is None or self.__file_mtime != os.path.getmtime(self.filename):
            self.__file_mtime = os.path.getmtime(self.filename)
            self.config = configparser.ConfigParser()
            self.config.read(self.filename)

    def get(self, name, default=None):
        self.check_file()
        d = self.config
        if not isinstance(name, list):
            name = [name]
        for i in name:
            if i in d:
                d = d[i]
            else:
                if default is None:
                    raise LookupError('Missing attribute {} in file {}'.format('.'.join(name), self.filename))
                return default
        return d

    def getList(self, name, default=[]):
        return [i.strip() for i in self.get(name, ','.join(default)).split(',')]

    def has(self, name):
        try:
            self.get(name)
            return True
        except Exception:
            pass
        return False
