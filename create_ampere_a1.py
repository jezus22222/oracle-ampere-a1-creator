#!/usr/bin/env python3
"""
Oracle Cloud Ampere A1 Instance Creator
- 4 OCPU, 24 GB RAM, 50 GB boot volume
- Ubuntu 22.04
- Rate-limit-aware with exponential backoff
- Runs as fast as Oracle allows without getting blocked
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import oci
from oci.exceptions import ServiceError, RequestException

# ============================================================
# CONFIGURATION - EDIT THESE VALUES
# ============================================================
# OCI Config file (created via 'oci setup config' or manually)
OCI_CONFIG_FILE = "~/.oci/config"
OCI_PROFILE = "DEFAULT"

# Instance configuration
INSTANCE_SHAPE = "VM.Standard.A1.Flex"  # Ampere A1 Flex
OCPU_COUNT = 4
MEMORY_IN_GBS = 24
BOOT_VOLUME_SIZE_IN_GBS = 50
AVAILABILITY_DOMAIN = None  # None = try all ADs in order, or specify specific AD name

# Ubuntu 22.04 image
UBUNTU_22_04_IMAGE_NAME = "Canonical Ubuntu 22.04"

# SSH PRIVATE KEY for INSTANCE ACCESS (different from the OCI API signing key).
# NEVER hardcode a private key here. The key is loaded from an external file:
# set the SSH_PRIVATE_KEY_FILE environment variable, or place your key at the
# default path below. Only the PUBLIC key derived from it is sent to OCI.
SSH_PRIVATE_KEY_FILE = os.environ.get("SSH_PRIVATE_KEY_FILE", "~/.ssh/id_rsa")

# Rate limiting configuration
INITIAL_DELAY_SECONDS = 2      # Start with 2s between attempts
MAX_DELAY_SECONDS = 60         # Cap at 60s
MAX_RETRIES = 100              # Keep trying for a long time
BACKOFF_MULTIPLIER = 1.5       # Exponential backoff factor
JITTER_FACTOR = 0.2            # ±20% jitter to avoid thundering herd

# Maximum total runtime in seconds (None = no limit)
MAX_RUNTIME_SECONDS = None  # e.g., 3600 for 1 hour max

# Dashboard integration
DASHBOARD_STATUS_FILE = os.environ.get(
    "DASHBOARD_STATUS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_status.json")
)

# Logging
LOG_LEVEL = logging.INFO
LOG_FILE = "ampere_a1_creation.log"

# ============================================================
# SETUP LOGGING
# ============================================================
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ============================================================
# DASHBOARD INTEGRATION
# ============================================================
def update_dashboard(status: str = None, message: str = None, current_ad: str = None,
                     total_attempts: int = None, instance_details: dict = None,
                     log_entry: dict = None, script_running: bool = None):
    """Update dashboard status file."""
    try:
        data = {}
        if os.path.exists(DASHBOARD_STATUS_FILE):
            with open(DASHBOARD_STATUS_FILE, 'r') as f:
                data = json.load(f)

        if status is not None:
            data['status'] = status
        if message is not None:
            data['message'] = message
        if current_ad is not None:
            data['current_ad'] = current_ad
        if total_attempts is not None:
            data['total_attempts'] = total_attempts
        if instance_details is not None:
            data['instance_details'] = instance_details
        if script_running is not None:
            data['script_running'] = script_running
        if 'start_time' not in data:
            data['start_time'] = time.time()
        data['last_update'] = time.time()

        # Handle log entries
        if log_entry:
            if 'logs' not in data:
                data['logs'] = []
            data['logs'].insert(0, log_entry)
            # Keep only last 50 logs
            data['logs'] = data['logs'][:50]

        with open(DASHBOARD_STATUS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.debug(f"Dashboard update failed: {e}")


def log_dashboard(level: str, message: str, ad_num: int = None, attempt_num: int = None):
    """Add a log entry to dashboard with AD and attempt info."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    ad_str = f"AD{ad_num}" if ad_num else "N/A"
    attempt_str = f"#{attempt_num}" if attempt_num else ""
    formatted_msg = f"[{timestamp}] [{ad_str}] [{attempt_str}] {message}" if attempt_str else f"[{timestamp}] [{ad_str}] {message}"
    update_dashboard(log_entry={"type": level, "message": formatted_msg, "timestamp": timestamp, "ad": ad_num, "attempt": attempt_num})


# ============================================================
# OCI CLIENT SETUP
# ============================================================
def create_oci_clients():
    """Create OCI service clients from config file."""
    config = oci.config.from_file(OCI_CONFIG_FILE, OCI_PROFILE)

    # Use default retry strategy, we implement our own rate-limit handling in run_with_rate_limit_handling
    compute_client = oci.core.ComputeClient(config)
    network_client = oci.core.VirtualNetworkClient(config)
    blockstorage_client = oci.core.BlockstorageClient(config)
    identity_client = oci.identity.IdentityClient(config)

    return compute_client, network_client, blockstorage_client, identity_client, config


# ============================================================
# HELPER FUNCTIONS
# ============================================================
def get_availability_domains(identity_client, compartment_id: str) -> list:
    """Get list of availability domains in the compartment."""
    try:
        response = identity_client.list_availability_domains(compartment_id=compartment_id)
        return response.data
    except ServiceError as e:
        logger.error(f"Failed to list availability domains: {e}")
        raise


def get_ubuntu_image(compute_client, compartment_id: str, shape: str) -> Optional[oci.core.models.Image]:
    """Find Ubuntu 22.04 image compatible with the shape."""
    try:
        # List images with Ubuntu 22.04 in name
        response = oci.pagination.list_call_get_all_results(
            compute_client.list_images,
            compartment_id=compartment_id,
            operating_system="Canonical Ubuntu",
            operating_system_version="22.04",
            shape=shape,
            sort_by="TIMECREATED",
            sort_order="DESC"
        )
        images = response.data

        if not images:
            logger.warning("No Ubuntu 22.04 images found for shape, trying broader search...")
            response = oci.pagination.list_call_get_all_results(
                compute_client.list_images,
                compartment_id=compartment_id,
                operating_system="Canonical Ubuntu",
                shape=shape,
                sort_by="TIMECREATED",
                sort_order="DESC"
            )
            images = response.data

        if images:
            # Prefer latest Ubuntu 22.04
            for img in images:
                if "22.04" in img.display_name:
                    logger.info(f"Selected image: {img.display_name} ({img.id})")
                    return img
            logger.info(f"Selected image: {images[0].display_name} ({images[0].id})")
            return images[0]

        return None
    except ServiceError as e:
        logger.error(f"Failed to list images: {e}")
        raise


def get_or_create_vcn(network_client, compartment_id: str, availability_domain: str) -> oci.core.models.Vcn:
    """Get existing VCN or create a new one."""
    try:
        # Look for existing VCN
        response = oci.pagination.list_call_get_all_results(
            network_client.list_vcns,
            compartment_id=compartment_id,
            display_name="ampere-a1-vcn"
        )
        if response.data:
            vcn = response.data[0]
            logger.info(f"Using existing VCN: {vcn.display_name} ({vcn.id})")
            return vcn
    except ServiceError:
        pass

    # Create new VCN
    logger.info("Creating new VCN...")
    create_vcn_details = oci.core.models.CreateVcnDetails(
        cidr_block="10.0.0.0/16",
        compartment_id=compartment_id,
        display_name="ampere-a1-vcn",
        dns_label="amperea1"
    )
    response = network_client.create_vcn(create_vcn_details)
    vcn = oci.wait_until(
        network_client,
        network_client.get_vcn(response.data.id),
        "lifecycle_state",
        "AVAILABLE",
        max_wait_seconds=300
    ).data
    logger.info(f"Created VCN: {vcn.display_name} ({vcn.id})")
    return vcn


def get_or_create_subnet(network_client, compartment_id: str, vcn_id: str, availability_domain: str) -> oci.core.models.Subnet:
    """Get existing subnet or create a new one."""
    try:
        response = oci.pagination.list_call_get_all_results(
            network_client.list_subnets,
            compartment_id=compartment_id,
            vcn_id=vcn_id,
            display_name="ampere-a1-subnet"
        )
        if response.data:
            subnet = response.data[0]
            logger.info(f"Using existing subnet: {subnet.display_name} ({subnet.id})")
            return subnet
    except ServiceError:
        pass

    # Create new subnet
    logger.info("Creating new subnet...")
    create_subnet_details = oci.core.models.CreateSubnetDetails(
        cidr_block="10.0.1.0/24",
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        availability_domain=availability_domain,
        display_name="ampere-a1-subnet",
        dns_label="amperea1",
        prohibit_public_ip_on_vnic=False
    )
    response = network_client.create_subnet(create_subnet_details)
    subnet = oci.wait_until(
        network_client,
        network_client.get_subnet(response.data.id),
        "lifecycle_state",
        "AVAILABLE",
        max_wait_seconds=300
    ).data
    logger.info(f"Created subnet: {subnet.display_name} ({subnet.id})")
    return subnet


def get_or_create_security_list(network_client, compartment_id: str, vcn_id: str) -> oci.core.models.SecurityList:
    """Get or create security list allowing SSH."""
    try:
        response = oci.pagination.list_call_get_all_results(
            network_client.list_security_lists,
            compartment_id=compartment_id,
            vcn_id=vcn_id,
            display_name="ampere-a1-security-list"
        )
        if response.data:
            sl = response.data[0]
            logger.info(f"Using existing security list: {sl.display_name} ({sl.id})")
            return sl
    except ServiceError:
        pass

    logger.info("Creating new security list...")
    create_sl_details = oci.core.models.CreateSecurityListDetails(
        compartment_id=compartment_id,
        vcn_id=vcn_id,
        display_name="ampere-a1-security-list",
        egress_security_rules=[
            oci.core.models.EgressSecurityRule(
                destination="0.0.0.0/0",
                protocol="all",
                is_stateless=False
            )
        ],
        ingress_security_rules=[
            oci.core.models.IngressSecurityRule(
                source="0.0.0.0/0",
                protocol="6",  # TCP
                is_stateless=False,
                tcp_options=oci.core.models.TcpOptions(
                    destination_port_range=oci.core.models.PortRange(min=22, max=22)
                )
            ),
            oci.core.models.IngressSecurityRule(
                source="0.0.0.0/0",
                protocol="1",  # ICMP
                is_stateless=False
            )
        ]
    )
    response = network_client.create_security_list(create_sl_details)
    sl = oci.wait_until(
        network_client,
        network_client.get_security_list(response.data.id),
        "lifecycle_state",
        "AVAILABLE",
        max_wait_seconds=300
    ).data
    logger.info(f"Created security list: {sl.display_name} ({sl.id})")
    return sl


def get_ssh_public_key() -> str:
    """Load the SSH private key from file and derive its public key."""
    from cryptography.hazmat.primitives import serialization

    key_path = os.path.expanduser(SSH_PRIVATE_KEY_FILE)
    if not os.path.isfile(key_path):
        raise FileNotFoundError(
            f"SSH private key not found at '{key_path}'. "
            "Set the SSH_PRIVATE_KEY_FILE environment variable or place your "
            "key at the default path."
        )
    with open(key_path, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    public_key = private_key.public_key()
    public_key_openssh = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH
    )
    return public_key_openssh.decode().strip()


def create_instance(compute_client, compartment_id: str, availability_domain: str,
                   subnet_id: str, image_id: str, shape: str, ocpus: int, memory_gb: int,
                   boot_volume_size_gb: int) -> oci.core.models.Instance:
    """Create the Ampere A1 instance."""
    ssh_public_key = get_ssh_public_key()

    # Get shape config for Ampere A1 Flex
    shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(
        ocpus=ocpus,
        memory_in_gbs=memory_gb
    )

    # Source details with boot volume configuration
    source_details = oci.core.models.InstanceSourceViaImageDetails(
        source_type="image",
        image_id=image_id,
        boot_volume_size_in_gbs=boot_volume_size_gb,
        boot_volume_vpus_per_gb=20  # Balanced performance
    )

    # Instance details
    instance_details = oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment_id,
        availability_domain=availability_domain,
        shape=shape,
        shape_config=shape_config,
        display_name=f"ampere-a1-{OCPU_COUNT}c-{MEMORY_IN_GBS}g-{int(time.time())}",
        source_details=source_details,
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id,
            assign_public_ip=True,
            display_name="primary-vnic"
        ),
        metadata={
            "ssh_authorized_keys": ssh_public_key,
            "user_data": ""  # No cloud-init user data needed
        },
        launch_options=oci.core.models.LaunchOptions(
            boot_volume_type="PARAVIRTUALIZED",
            network_type="PARAVIRTUALIZED",
            is_pv_encryption_in_transit_enabled=True
        ),
        is_pv_encryption_in_transit_enabled=True
    )

    logger.info(f"Launching instance: {instance_details.display_name}")
    logger.info(f"  Shape: {shape} ({ocpus} OCPU, {memory_gb} GB RAM)")
    logger.info(f"  Boot volume: {boot_volume_size_gb} GB")
    logger.info(f"  Image: {image_id}")
    logger.info(f"  Subnet: {subnet_id}")

    response = compute_client.launch_instance(instance_details)

    # Wait for instance to be RUNNING
    instance = oci.wait_until(
        compute_client,
        compute_client.get_instance(response.data.id),
        "lifecycle_state",
        "RUNNING",
        max_wait_seconds=600,
        succeed_on_not_found=False
    ).data

    return instance


def get_instance_ips(network_client, compute_client, instance_id: str,
                     compartment_id: str) -> tuple:
    """Get (public_ip, private_ip) from the instance's VNIC attachments.

    OCI Instance objects don't carry IPs directly - they live on the VNIC.
    """
    try:
        response = oci.pagination.list_call_get_all_results(
            compute_client.list_vnic_attachments,
            compartment_id=compartment_id,
            instance_id=instance_id
        )

        for attachment in response.data:
            vnic = network_client.get_vnic(attachment.vnic_id).data
            if vnic.public_ip or vnic.private_ip:
                return vnic.public_ip, vnic.private_ip
    except Exception as e:
        logger.warning(f"Could not get instance IPs: {e}")
    return None, None


# ============================================================
# MAIN EXECUTION WITH RATE LIMIT HANDLING
# ============================================================
def run_with_rate_limit_handling(attempt_func, *args, ad_num: int = None, attempt_num: int = None, **kwargs):
    """
    Execute a function with custom rate limit handling.
    Catches 429 errors and applies exponential backoff with jitter.
    """
    delay = INITIAL_DELAY_SECONDS
    attempt = 0

    while attempt < MAX_RETRIES:
        attempt += 1
        try:
            logger.info(f"Attempt {attempt}/{MAX_RETRIES}...")
            result = attempt_func(*args, **kwargs)
            logger.info(f"✓ Success on attempt {attempt}")
            return result

        except ServiceError as e:
            if e.status == 429:  # Rate limited
                logger.warning(f"Rate limited (429): {e.message}")
                log_dashboard("warning", f"Rate limited (429), waiting {delay:.1f}s...", ad_num=ad_num, attempt_num=attempt)
                logger.info(f"Waiting {delay:.1f}s before retry...")
                time.sleep(delay)
                delay = min(delay * BACKOFF_MULTIPLIER, MAX_DELAY_SECONDS)
                # Add jitter
                import random
                jitter = delay * JITTER_FACTOR * (random.random() * 2 - 1)
                delay = max(INITIAL_DELAY_SECONDS, delay + jitter)
                continue

            elif e.status in (500, 502, 503, 504):
                logger.warning(f"Server error ({e.status}): {e.message}")
                log_dashboard("warning", f"Server error ({e.status}): {e.message}, waiting {delay:.1f}s...", ad_num=ad_num, attempt_num=attempt)
                logger.info(f"Waiting {delay:.1f}s before retry...")
                time.sleep(delay)
                delay = min(delay * BACKOFF_MULTIPLIER, MAX_DELAY_SECONDS)
                continue

            elif e.status == 400 and "limit" in str(e.message).lower():
                logger.warning(f"Limit exceeded: {e.message}")
                log_dashboard("warning", f"Limit exceeded: {e.message}, waiting {delay:.1f}s...", ad_num=ad_num, attempt_num=attempt)
                logger.info(f"Waiting {delay:.1f}s before retry...")
                time.sleep(delay)
                delay = min(delay * BACKOFF_MULTIPLIER, MAX_DELAY_SECONDS)
                continue

            else:
                logger.error(f"Non-retryable error ({e.status}): {e.message}")
                raise

        except RequestException as e:
            logger.warning(f"Network error: {e}")
            log_dashboard("warning", f"Network error: {e}, waiting {delay:.1f}s...", ad_num=ad_num, attempt_num=attempt)
            logger.info(f"Waiting {delay:.1f}s before retry...")
            time.sleep(delay)
            delay = min(delay * BACKOFF_MULTIPLIER, MAX_DELAY_SECONDS)
            continue

        except Exception as e:
            logger.error(f"Unexpected error: {type(e).__name__}: {e}")
            raise

    raise Exception(f"Max retries ({MAX_RETRIES}) exceeded")


def main():
    start_time = time.time()

    # Setup OCI clients
    compute_client, network_client, blockstorage_client, identity_client, config = create_oci_clients()
    compartment_id = config["tenancy"]  # Use tenancy as compartment (root)

    logger.info("=" * 60)
    logger.info("Oracle Cloud Ampere A1 Instance Creator")
    logger.info(f"Target: {OCPU_COUNT} OCPU, {MEMORY_IN_GBS} GB RAM, {BOOT_VOLUME_SIZE_IN_GBS} GB")
    logger.info(f"Region: {config.get('region', 'unknown')}")
    logger.info(f"Image: Ubuntu 22.04")
    if MAX_RUNTIME_SECONDS:
        logger.info(f"Max runtime: {MAX_RUNTIME_SECONDS}s")
    logger.info("=" * 60)

    log_dashboard("info", "Script started", ad_num=0, attempt_num=0)

    # Get availability domains
    ads = run_with_rate_limit_handling(get_availability_domains, identity_client, compartment_id)
    if not ads:
        raise Exception("No availability domains found")

    # Determine which ADs to try
    if AVAILABILITY_DOMAIN:
        ad_list = [ad for ad in ads if ad.name == AVAILABILITY_DOMAIN]
        if not ad_list:
            raise Exception(f"Availability domain {AVAILABILITY_DOMAIN} not found")
    else:
        ad_list = ads
        logger.info(f"Will try {len(ad_list)} availability domains: {[ad.name for ad in ad_list]}")
        log_dashboard("info", f"Will try {len(ad_list)} availability domains: {[ad.name for ad in ad_list]}", ad_num=0, attempt_num=0)

    # Get Ubuntu 22.04 image (same for all ADs in same region)
    image = run_with_rate_limit_handling(get_ubuntu_image, compute_client, compartment_id, INSTANCE_SHAPE, ad_num=0, attempt_num=0)
    if not image:
        raise Exception("No Ubuntu 22.04 image found for Ampere A1 shape")

    # Try each availability domain
    attempt_counter = 0
    for ad_index, ad in enumerate(ad_list):
        # Check max runtime
        if MAX_RUNTIME_SECONDS and (time.time() - start_time) > MAX_RUNTIME_SECONDS:
            logger.error(f"Max runtime ({MAX_RUNTIME_SECONDS}s) exceeded")
            update_dashboard(status="failed", message="Max runtime exceeded")
            log_dashboard("error", "Max runtime exceeded", ad_num=ad_index+1, attempt_num=attempt_counter)
            raise Exception("Max runtime exceeded")

        availability_domain = ad.name
        logger.info(f"Trying availability domain {ad_index + 1}/{len(ad_list)}: {availability_domain}")
        update_dashboard(current_ad=availability_domain, message=f"Trying AD {ad_index + 1}/{len(ad_list)}")
        log_dashboard("info", f"Trying availability domain {ad_index + 1}/{len(ad_list)}: {availability_domain}", ad_num=ad_index+1, attempt_num=attempt_counter)

        try:
            # Setup networking for this AD
            log_dashboard("info", f"Setting up networking in {availability_domain}...", ad_num=ad_index+1, attempt_num=attempt_counter)
            vcn = run_with_rate_limit_handling(get_or_create_vcn, network_client, compartment_id, availability_domain, ad_num=ad_index+1, attempt_num=attempt_counter)
            subnet = run_with_rate_limit_handling(get_or_create_subnet, network_client, compartment_id, vcn.id, availability_domain, ad_num=ad_index+1, attempt_num=attempt_counter)
            security_list = run_with_rate_limit_handling(get_or_create_security_list, network_client, compartment_id, vcn.id, ad_num=ad_index+1, attempt_num=attempt_counter)

            # Associate security list with subnet if not already
            try:
                subnet = network_client.get_subnet(subnet.id).data
                if security_list.id not in subnet.security_list_ids:
                    network_client.update_subnet(
                        subnet.id,
                        oci.core.models.UpdateSubnetDetails(
                            security_list_ids=subnet.security_list_ids + [security_list.id]
                        )
                    )
                    logger.info("Associated security list with subnet")
            except Exception as e:
                logger.warning(f"Could not associate security list: {e}")

            # Create instance
            log_dashboard("info", f"Launching instance in {availability_domain}...", ad_num=ad_index+1, attempt_num=attempt_counter+1)
            attempt_counter += 1
            update_dashboard(total_attempts=attempt_counter)
            instance = run_with_rate_limit_handling(
                create_instance,
                compute_client, compartment_id, availability_domain,
                subnet.id, image.id, INSTANCE_SHAPE, OCPU_COUNT, MEMORY_IN_GBS, BOOT_VOLUME_SIZE_IN_GBS,
                ad_num=ad_index+1, attempt_num=attempt_counter
            )

            # Get IPs from VNIC attachments (Instance objects don't carry them)
            public_ip, private_ip = get_instance_ips(
                network_client, compute_client, instance.id, compartment_id
            )

            # Success summary
            logger.info("=" * 60)
            logger.info("✓ INSTANCE CREATED SUCCESSFULLY")
            logger.info("=" * 60)
            logger.info(f"Instance Name: {instance.display_name}")
            logger.info(f"Instance OCID: {instance.id}")
            logger.info(f"Shape: {instance.shape} ({instance.shape_config.ocpus} OCPU, {instance.shape_config.memory_in_gbs} GB)")
            logger.info(f"Availability Domain: {instance.availability_domain}")
            logger.info(f"State: {instance.lifecycle_state}")
            logger.info(f"Private IP: {private_ip or 'Not yet assigned'}")
            logger.info(f"Public IP: {public_ip or 'Not yet assigned'}")
            logger.info(f"SSH Command: ssh -i <your-key> ubuntu@{public_ip or private_ip or '<pending>'}")
            logger.info(f"Total time: {time.time() - start_time:.1f}s")
            logger.info("=" * 60)

            # Save instance details to file
            output = {
                "instance_id": instance.id,
                "instance_name": instance.display_name,
                "public_ip": public_ip,
                "private_ip": private_ip,
                "shape": instance.shape,
                "ocpus": instance.shape_config.ocpus if instance.shape_config else OCPU_COUNT,
                "memory_gb": instance.shape_config.memory_in_gbs if instance.shape_config else MEMORY_IN_GBS,
                "availability_domain": instance.availability_domain,
                "region": config.get("region", "unknown"),
                "ssh_user": "ubuntu",
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            with open("instance_details.json", "w") as f:
                json.dump(output, f, indent=2)
            logger.info("Instance details saved to instance_details.json")

            # Update dashboard with success
            update_dashboard(
                status="success",
                message="Instance created successfully!",
                instance_details=output,
                script_running=False
            )
            log_dashboard("success", f"Instance created: {instance.display_name} ({public_ip or 'pending IP'})", ad_num=ad_index+1, attempt_num=attempt_counter)

            return output

        except Exception as e:
            # Check if it's a capacity issue (500 with "Out of host capacity")
            if isinstance(e, ServiceError) and e.status == 500 and "Out of host capacity" in str(e.message):
                logger.warning(f"No capacity in {availability_domain}, trying next AD...")
                log_dashboard("warning", f"No capacity in {availability_domain}, trying next AD...", ad_num=ad_index+1, attempt_num=attempt_counter)
                continue
            # Check if it's a rate limit that we've exhausted
            elif isinstance(e, Exception) and "Max retries" in str(e):
                logger.error(f"Exhausted retries in {availability_domain}, trying next AD...")
                log_dashboard("error", f"Exhausted retries in {availability_domain}, trying next AD...", ad_num=ad_index+1, attempt_num=attempt_counter)
                continue
            else:
                logger.error(f"Error in {availability_domain}: {e}")
                log_dashboard("error", f"Error in {availability_domain}: {e}", ad_num=ad_index+1, attempt_num=attempt_counter)
                raise

    update_dashboard(status="failed", message="Failed in all availability domains", script_running=False)
    log_dashboard("error", f"Failed to create instance in all {len(ad_list)} availability domains", ad_num=0, attempt_num=attempt_counter)
    raise Exception(f"Failed to create instance in all {len(ad_list)} availability domains")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        update_dashboard(status="failed", message="Interrupted by user", script_running=False)
        log_dashboard("error", "Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        update_dashboard(status="failed", message=f"Fatal error: {e}", script_running=False)
        log_dashboard("error", f"Fatal error: {e}")
        sys.exit(1)