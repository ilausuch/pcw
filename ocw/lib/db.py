from webui.settings import PCWConfig
from ..models import Instance
from ..models import StateChoice
from ..models import ProviderChoice
from django.db import transaction
from django.db.models import F
from django.utils import timezone
import time
import json
import dateutil.parser
from .emailnotify import send_mail, send_leftover_notification
import traceback
import logging
from .azure import Azure
from .EC2 import EC2
from .gce import GCE
from datetime import datetime
from datetime import timedelta
from ocw.apps import getScheduler

logger = logging.getLogger(__name__)
__running = False
__last_update = None


@transaction.atomic
def sync_csp_to_local_db(pc_instances, provider, namespace):
    t_now = timezone.now()
    Instance.objects.filter(provider=provider, vault_namespace=namespace).update(active=False)

    for i in pc_instances:
        if i.provider != provider:
            raise ValueError('Instance {} does not belong to {}'.format(i, provider))
        if i.vault_namespace != namespace:
            raise ValueError('Instance {} does not belong to {}'.format(i, namespace))

        if Instance.objects.filter(provider=i.provider, instance_id=i.instance_id, vault_namespace=namespace).exists():
            logger.debug("[%s] Update instance %s:%s", namespace, provider, i.instance_id)
            o = Instance.objects.get(provider=i.provider, instance_id=i.instance_id, vault_namespace=namespace)
            if o.region != i.region:
                logger.info("[%s] Instance %s:%s changed region from %s to %s",
                            namespace, provider, i.instance_id, o.region, i.region)
                o.region = i.region
            if o.state == StateChoice.DELETED:
                logger.info("[%s] %s:%s instance which still exists has DELETED state in DB. Reactivating %s",
                            namespace, provider, i.instance_id, i.all_time_fields())
                o.first_seen = i.first_seen
            if o.state != StateChoice.DELETING:
                o.state = StateChoice.ACTIVE
        else:
            logger.debug("[%s] Create instance %s:%s", namespace, provider, i.instance_id)
            o = Instance(
                provider=provider,
                vault_namespace=namespace,
                first_seen=i.first_seen,
                instance_id=i.instance_id,
                state=StateChoice.ACTIVE,
                ttl=i.ttl,
                region=i.region
            )
        o.csp_info = i.csp_info
        o.last_seen = t_now
        o.active = True
        o.age = o.last_seen - o.first_seen
        o.save()
    Instance.objects.filter(provider=provider, vault_namespace=namespace, active=False). \
        update(state=StateChoice.DELETED)


def tag_to_boolean(tag_name, csp_info):
    try:
        return bool(csp_info['tags'][tag_name])
    except KeyError:
        return False


def ec2_to_json(i):
    info = {
        'state': i.state['Name'],
        'image_id': i.image_id,
        'instance_lifecycle': i.instance_lifecycle,
        'instance_type': i.instance_type,
        'kernel_id': i.kernel_id,
        'launch_time': i.launch_time.isoformat(),
        'public_ip_address': i.public_ip_address,
        'security_groups': [sg['GroupName'] for sg in i.security_groups],
        'sriov_net_support': i.sriov_net_support,
        'tags': {t['Key']: t['Value'] for t in i.tags} if i.tags else {}
    }
    if i.state_reason:
        info['state_reason'] = i.state_reason['Message']

    if i.image:
        img = i.image
        info['image'] = {
            'image_id': img.image_id
        }
        # This happen, if the image was already deleted
        if img.meta.data is not None:
            info['image']['name'] = img.name

    return info


def ec2_to_local_instance(instance, vault_namespace, region):
    csp_info = ec2_to_json(instance)
    return Instance(
        provider=ProviderChoice.EC2,
        vault_namespace=vault_namespace,
        first_seen=dateutil.parser.parse(csp_info.get('launch_time', str(timezone.now()))),
        instance_id=instance.instance_id,
        state=StateChoice.ACTIVE,
        region=region,
        csp_info=json.dumps(csp_info, ensure_ascii=False),
        ttl=timedelta(seconds=int(csp_info['tags'].get(
            'openqa_ttl', PCWConfig.get_feature_property('updaterun', 'default_ttl', vault_namespace)))),
        ignore=tag_to_boolean('pcw_ignore', csp_info)
    )


def azure_to_json(i):
    info = {
        'tags': i.tags if i.tags else {},
        'name': i.name,
        'id': i.id,
        'type': i.type,
        'location': i.location
    }
    if (i.tags is not None and 'openqa_created_date' in i.tags):
        info['launch_time'] = i.tags.get('openqa_created_date')
    return info


def azure_to_local_instance(instance, vault_namespace):
    csp_info = azure_to_json(instance)
    return Instance(
        provider=ProviderChoice.AZURE,
        vault_namespace=vault_namespace,
        first_seen=dateutil.parser.parse(csp_info.get('launch_time', str(timezone.now()))),
        instance_id=instance.name,
        region=instance.location,
        csp_info=json.dumps(csp_info, ensure_ascii=False),
        ttl=timedelta(seconds=int(csp_info['tags'].get(
            'openqa_ttl', PCWConfig.get_feature_property('updaterun', 'default_ttl', vault_namespace)))),
        ignore=tag_to_boolean('pcw_ignore', csp_info)
    )


def gce_to_json(i):
    info = {
        'tags': {m['key']: m['value'] for m in i['metadata']['items']} if 'items' in i['metadata'] else {},
        'name': i['name'],
        'id': i['id'],
        'machineType': GCE.url_to_name(i['machineType']),
        'zone': GCE.url_to_name(i['zone']),
        'status': i['status'],
        'launch_time': i['creationTimestamp'],
        'creation_time': i['creationTimestamp'],
    }
    if 'openqa_created_date' in info['tags']:
        info['launch_time'] = info['tags']['openqa_created_date']
    info['tags'].pop('sshKeys', '')
    return info


def gce_to_local_instance(instance, vault_namespace):
    csp_info = gce_to_json(instance)
    return Instance(
        provider=ProviderChoice.GCE,
        vault_namespace=vault_namespace,
        first_seen=dateutil.parser.parse(csp_info.get('launch_time', str(timezone.now()))),
        instance_id=instance['id'],
        region=GCE.url_to_name(instance['zone']),
        csp_info=json.dumps(csp_info, ensure_ascii=False),
        ttl=timedelta(seconds=int(csp_info['tags'].get(
            'openqa_ttl', PCWConfig.get_feature_property('updaterun', 'default_ttl', vault_namespace)))),
        ignore=tag_to_boolean('pcw_ignore', csp_info)
    )


def _update_provider(name, vault_namespace):
    if 'azure' in name:
        instances = Azure(vault_namespace).list_resource_groups()
        instances = [azure_to_local_instance(i, vault_namespace) for i in instances]
        logger.info("Got %d resources groups from Azure", len(instances))
        sync_csp_to_local_db(instances, ProviderChoice.AZURE, vault_namespace)

    if 'ec2' in name:
        instances = []
        for region in EC2(vault_namespace).all_regions:
            instances_csp = EC2(vault_namespace).list_instances(region=region)
            instances += [ec2_to_local_instance(i, vault_namespace, region) for i in instances_csp]
            logger.info("Got %d instances from EC2 in region %s", len(instances), region)
        sync_csp_to_local_db(instances, ProviderChoice.EC2, vault_namespace)

    if 'gce' in name:
        instances = GCE(vault_namespace).list_all_instances()
        instances = [gce_to_local_instance(i, vault_namespace) for i in instances]
        logger.info("Got %d instances from GCE", len(instances))
        sync_csp_to_local_db(instances, ProviderChoice.GCE, vault_namespace)


def update_run():
    '''
    Each update is using Instance.active to mark the model is still availalbe on CSP.
    Instance.state is used to reflect the "local" state, e.g. if someone triggered a delete, the
    state will moved to DELETING. If the instance is gone from CSP, the state will set to DELETED.
    '''
    global __running, __last_update
    __running = True
    max_retries = 3
    error_occured = False
    for namespace in PCWConfig.get_namespaces_for('default'):
        for provider in PCWConfig.get_providers_for('default', namespace):
            logger.info("[%s] Check provider %s", namespace, provider)
            email_text = set()
            for n in range(max_retries):
                try:
                    _update_provider(provider, namespace)
                except Exception:
                    logger.exception("[%s] Update failed for %s", namespace, provider)
                    email_text.add(traceback.format_exc())
                    time.sleep(5)
                else:
                    break
            else:
                error_occured = True
                send_mail('Error on update {} in namespace {}'.format(provider, namespace),
                          "\n{}\n".format('#'*79).join(email_text))

    auto_delete_instances()
    send_leftover_notification()
    __running = False
    if not error_occured:
        __last_update = datetime.now(timezone.utc)

    if not getScheduler().get_job('update_db'):
        init_cron()


def delete_instance(instance):
    logger.debug("[%s] Delete instance %s:%s", instance.vault_namespace, instance.provider, instance.instance_id)
    if (instance.provider == ProviderChoice.AZURE):
        Azure(instance.vault_namespace).delete_resource(instance.instance_id)
    elif (instance.provider == ProviderChoice.EC2):
        EC2(instance.vault_namespace).delete_instance(instance.region, instance.instance_id)
    elif (instance.provider == ProviderChoice.GCE):
        GCE(instance.vault_namespace).delete_instance(instance.instance_id, instance.region)
    else:
        raise NotImplementedError(
            "Provider({}).delete() isn't implemented".format(instance.provider))

    instance.state = StateChoice.DELETING
    instance.save()


def auto_delete_instances():
    for namespace in PCWConfig.get_namespaces_for('default'):
        o = Instance.objects
        o = o.filter(state=StateChoice.ACTIVE, vault_namespace=namespace, ttl__gt=timedelta(0),
                     age__gte=F('ttl')).exclude(csp_info__icontains='pcw_ignore')
        email_text = set()
        for i in o:
            logger.info("[%s] TTL expire for instance %s:%s %s", i.vault_namespace,
                        i.provider, i.instance_id, i.all_time_fields())
            try:
                delete_instance(i)
            except Exception:
                msg = "[{}] Deleting instance ({}:{}) failed".format(i.vault_namespace, i.provider, i.instance_id)
                logger.exception(msg)
                email_text.add("{}\n\n{}".format(msg, traceback.format_exc()))

        if len(email_text) > 0:
            send_mail('[{}] Error on auto deleting instance(s)'.format(namespace),
                      "\n{}\n".format('#'*79).join(email_text))


def is_updating():
    global __running
    return __running


def last_update():
    global __last_update
    return __last_update if __last_update is not None else ''


def start_update():
    global __running
    if not __running:
        getScheduler().get_job('update_db').reschedule(trigger='date', run_date=datetime.now(timezone.utc))


def init_cron():
    getScheduler().add_job(update_run, trigger='interval', minutes=5, id='update_db')
