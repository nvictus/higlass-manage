import click
import docker
import json
import os
import os.path as op
import requests
import slugid
import sys
import time

from higlass_manage.common import (
    CONTAINER_PREFIX,
    NETWORK_PREFIX,
    REDIS_PREFIX,
    REDIS_CONF,
    HOME_DIR,
    TEMP_DIR
)


@click.command()
@click.option(
    "-n",
    "--hg-name",
    default="default",
    show_default=True,
    help="The name for this HiGlass instance",
    type=str,
)
@click.option(
    "-d",
    "--data-dir",
    help="The data directory the HiGlass instance will use. "
    "[default: {}/.higlass/containers/{{hg-name}}]".format(HOME_DIR),
    default=None,
    type=str,
)
@click.option(
    "-p",
    "--port",
    help="The port that the HiGlass instance should run on",
    default=8989,
    show_default=True,
    type=str,
)
@click.option(
    "-t",
    "--temp-dir",
    default=op.join(TEMP_DIR, "higlass-docker"),
    show_default=True,
    help="The temp directory to use",
    type=str,
)
@click.option(
    "-v",
    "--version",
    help='The version of the Docker container to use (or "local" for images '
    "built locally using higlass-docker)",
    default="latest",
    show_default=True,
    type=str,
)
@click.option(
    "-s",
    "--site-url",
    default=None,
    help="When creating an external-facing instance, enter its IP or hostname "
    "using this parameter",
    type=str,
)
@click.option(
    "-m",
    "--media-dir",
    default=None,
    help="Use a specific media directory for uploaded files. "
    "[default: {data-dir}/media]",
    type=str,
)
@click.option(
    "--public-data/--no-public-data",
    default=True,
    help="Include or exclude public data in the list of available tilesets. "
    "Default is to include it.",
)
@click.option(
    "--default-track-options",
    default=None,
    help="Specify a json file containing default track options",
)
@click.option(
    "--workers",
    default=None,
    help="Specify a custom number of workers for the uWSGI application server",
    type=int,
)
@click.option(
    "--use-redis",
    help="Initialize a Redis-based caching service and bind higlass instance to it",
    is_flag=True,
    default=False,
)
@click.option(
    "--redis-dir",
    help="Use a specific directory for Redis files. [default: {data-dir}/redis]",
    type=str,
)
@click.option(
    "--hg-repository",
    default="higlass/higlass-docker",
    show_default=True,
    help="The Docker repository to use for the HiGlass image",
    type=str,
)
@click.option(
    "--redis-repository",
    default="redis",
    show_default=True,
    help="The Docker repository to use for the Redis image",
    type=str,
)
@click.option(
    "--redis-tag",
    default="5.0.3-alpine",
    show_default=True,
    help="The Docker tag to use for the Redis image repository",
    type=str,
)
@click.option(
    "--redis-port",
    default=6379,
    show_default=True,
    help="The port to use for the Redis image",
    type=int,
)
@click.option(
    "-y", "--yes",
    help="Don't prompt if data directory does not exist",
    is_flag=True)
def start(
    hg_name,
    data_dir,
    port,
    temp_dir,
    version,
    site_url,
    media_dir,
    public_data,
    default_track_options,
    workers,
    use_redis,
    redis_dir,
    hg_repository,
    redis_repository,
    redis_tag,
    redis_port,
    yes,
):
    """
    Start a HiGlass instance

    """
    _start(
        hg_name,
        data_dir,
        port,
        temp_dir,
        version,
        site_url,
        media_dir,
        public_data,
        default_track_options,
        workers,
        use_redis,
        redis_dir,
        hg_repository,
        redis_repository,
        redis_tag,
        redis_port,
        yes,
    )


def _start(
    hg_name="default",
    data_dir=None,
    port=8989,
    temp_dir=op.join(TEMP_DIR, "higlass-docker"),
    version="latest",
    site_url=None,
    media_dir=None,
    public_data=True,
    default_track_options=None,
    workers=None,
    use_redis=False,
    redis_dir=None,
    hg_repository="higlass/higlass-docker",
    redis_repository="redis",
    redis_tag="5.0.3-alpine",
    redis_port=6379,
    yes=False,
):
    """
    Start a HiGlass instance
    """
    click.echo(
        'Starting HiGlass instance "{}" on port {}...\n'.format(hg_name, port)
    )

    hg_container_name = "{}-{}".format(CONTAINER_PREFIX, hg_name)

    if data_dir is None:
        container_root = op.join(HOME_DIR, ".higlass", "containers")
        data_dir = op.join(container_root, hg_name)

    data_dir = op.realpath(op.expanduser(data_dir))

    if not op.exists(data_dir) and not yes:
        if click.confirm(
            'Data directory "{}" does not exist. Create?'.format(data_dir),
            abort=True
        ):
            os.makedirs(data_dir)
            click.echo('Created data directory {}'.format(data_dir))

    else:
        click.echo('Using data directory {}'.format(data_dir))

    temp_dir = op.realpath(op.expanduser(temp_dir))
    if not op.exists(temp_dir):
        os.makedirs(temp_dir)

    client = docker.from_env()

    try:
        hg_container = client.containers.get(hg_container_name)
        sys.stderr.write("Stopping previously running container\n")
        hg_container.stop()
        hg_container.remove()
    except docker.errors.NotFound:
        # container isn't running so no need to stop it
        pass
    except requests.exceptions.ConnectionError:
        sys.stderr.write(
            "Error connecting to the Docker daemon, make sure it is started "
            "and you are logged in.\n"
        )
        return

    if use_redis:
        if redis_dir is None:
            redis_dir = op.join(data_dir, 'redis')
        redis_dir = op.realpath(os.expanduser(redis_dir))
        click.echo('Starting redis [redis_dir: {}]'.format(redis_dir))

        network_name = "{}-{}".format(NETWORK_PREFIX, hg_name)
        redis_name = "{}-{}".format(REDIS_PREFIX, hg_name)

        # set up a bridge network for Redis and higlass containers to share
        try:
            network_list = client.networks.list(names=[network_name])
            if network_list:
                network = client.networks.get(network_name)
                sys.stderr.write(
                    "Attempting to remove existing Docker network instance\n"
                )
                network.remove()
        except docker.errors.APIError:
            sys.stderr.write(
                "Error: Could not access Docker network list to remove "
                "existing network.\n"
            )
            sys.exit(-1)

        try:
            # https://docker-py.readthedocs.io/en/stable/networks.html
            network = client.networks.create(network_name, driver="bridge")
        except docker.errors.APIError as err:
            sys.stderr.write(
                "Error: Could not access Docker network ({}).\n".format(err)
            )
            sys.exit(-1)

        # clear up any running Redis container
        try:
            redis_container = client.containers.get(redis_name)
            sys.stderr.write("Stopping previously running Redis container\n")
            redis_container.stop()
            redis_container.remove()
        except docker.errors.NotFound:
            pass
        except requests.exceptions.ConnectionError:
            sys.stderr.write(
                "Error: Error connecting to the Docker daemon, make sure it "
                "is started and you are logged in.\n"
            )
            sys.exit(-1)

        # pull Redis image
        sys.stderr.write("Pulling {}:{}\n".format(redis_repository, redis_tag))
        sys.stderr.flush()
        redis_image = client.images.pull(redis_repository, tag=redis_tag)
        sys.stderr.write("done\n")
        sys.stderr.flush()

        # set up Redis container settings and environment
        if not op.exists(redis_dir):
            os.makedirs(redis_dir)

        redis_conf = os.path.join(
            os.path.dirname(os.path.realpath(__file__)), "redis", "redis.conf"
        )
        if not os.path.exists(redis_conf):
            sys.stderr.write(
                "Error: Could not locate Redis configuration file "
                "[{}]\n".format(redis_conf)
            )
            sys.exit(-1)

        redis_volumes = {
            redis_dir: {"bind": "/data", "mode": "rw"},
            redis_conf: {"bind": REDIS_CONF, "mode": "rw"},
        }

        redis_command = "redis-server {}".format(REDIS_CONF)

        try:
            # run Redis container
            redis_container = client.containers.run(
                redis_image,
                redis_command,
                name=redis_name,
                network=network_name,
                volumes=redis_volumes,
                detach=True,
            )
        except docker.errors.ContainerError as err:
            sys.stderr.write(
                "Error: Redis container could not be started\n{}\n".format(err)
            )
            sys.exit(-1)
        except docker.errors.ImageNotFound as err:
            sys.stderr.write(
                "Error: Redis container image could not be found"
                "\n{}\n".format(err)
            )
            sys.exit(-1)
        except docker.errors.APIError as err:
            sys.stderr.write(
                "Error: Redis container server ran into a fatal error"
                "\n{}\n".format(err)
            )
            sys.exit(-1)

    if version == "local":
        hg_image = client.images.get("image-default")
    else:
        sys.stderr.write("Pulling latest image... \n")
        sys.stderr.flush()
        hg_image = client.images.pull(hg_repository, version)
        sys.stderr.write("done\n")
        sys.stderr.flush()

    hg_environment = {}

    if site_url is not None:
        hg_environment["SITE_URL"] = site_url

    if workers is not None:
        hg_environment["WORKERS"] = workers

    sys.stderr.write("Data directory: {}\n".format(data_dir))
    sys.stderr.write("Temp directory: {}\n".format(temp_dir))
    hg_volumes = {
        temp_dir: {"bind": "/tmp", "mode": "rw"},
        data_dir: {"bind": "/data", "mode": "rw"},
    }

    if media_dir is not None:
        media_dir = os.realpath(os.expanduser(media_dir))
        hg_volumes[media_dir] = {"bind": "/media", "mode": "rw"}
        hg_environment["HIGLASS_MEDIA_ROOT"] = "/media"

    if not use_redis:
        hg_container = client.containers.run(
            hg_image,
            ports={80: port},
            volumes=hg_volumes,
            name=hg_container_name,
            environment=hg_environment,
            detach=True,
        )
    else:
        # add some environment variables to the higlass container
        hg_environment["REDIS_HOST"] = redis_name
        hg_environment["REDIS_PORT"] = redis_port
        # run the higlass container on the shared network with the Redis container
        hg_container = client.containers.run(
            hg_image,
            network=network_name,
            ports={80: port},
            volumes=hg_volumes,
            name=hg_container_name,
            environment=hg_environment,
            publish_all_ports=True,
            detach=True,
        )

    sys.stderr.write("Docker started: {}\n".format(hg_container_name))

    started = False
    counter = 1
    while not started:
        try:
            sys.stderr.write("sending request {}\n".format(counter))
            counter += 1
            req = requests.get(
                "http://localhost:{}/api/v1/viewconfs/?d=default".format(port),
                timeout=5,
            )
            # sys.stderr.write("request returned {} {}\n".format(req.status_code, req.content))

            if req.status_code != 200:
                sys.stderr.write(
                    "Non 200 status code returned ({}), waiting...\n".format(
                        req.status_code
                    )
                )
                time.sleep(0.5)
            else:
                started = True
        except requests.exceptions.ConnectionError:
            sys.stderr.write("Waiting to start (tilesets)...\n")
            time.sleep(0.5)
        except requests.exceptions.ReadTimout:
            sys.stderr.write("Request timed out\n")
            time.sleep(0.5)

    sys.stderr.write("public_data: {}\n".format(public_data))

    if not public_data or default_track_options is not None:
        # we're going to be changing the higlass js file so first we copy it to a location
        # with a new hash
        new_hash = slugid.nice()

        sed_command = """bash -c 'cp higlass-app/static/js/main.*.chunk.js higlass-app/static/js/main.{}.chunk.js'""".format(
            new_hash
        )

        ret = hg_container.exec_run(sed_command)

    if not public_data:
        config = json.loads(req.content.decode("utf-8"))
        config["trackSourceServers"] = ["/api/v1"]
        started = True
        # sys.stderr.write('config {}\n'.format(json.dumps(config, indent=2)))
        config = {"uid": "default_local", "viewconf": config}

        ret = hg_container.exec_run(
            """python higlass-server/manage.py shell --command="import tilesets.models as tm; o = tm.ViewConf.objects.get(uuid='default_local'); o.delete();" """
        )
        ret = requests.post(
            "http://localhost:{}/api/v1/viewconfs/".format(port), json=config
        )
        sys.stderr.write("ret: {}\n".format(ret.content))
        # ret = container.exec_run('echo "import tilesets.models as tm; tm.ViewConf.get(uuid={}default{}).delete()" | python higlass-server/manage.py shell'.format("'", "'"), tty=True)
        ret = hg_container.exec_run(
            """bash -c 'sed -i '"'"'s/"default"/"default_local"/g'"'"' higlass-app/static/js/main.*.chunk.js'""".format(
                new_hash
            )
        )
        sys.stderr.write("ret: {}\n".format(ret))

    if default_track_options is not None:
        with open(default_track_options, "r") as f:
            default_options_json = json.load(f)

            sed_command = """bash -c 'sed -i '"'"'s/assign({{}},this.props.options/assign({{defaultOptions: {} }},this.props.options/g'"'"' """.format(
                json.dumps(default_options_json)
            )
            sed_command += " higlass-app/static/js/main.*.chunk.js'"
            # sys.stderr.write("sed_command: {}\n".format(sed_command))

            ret = hg_container.exec_run(sed_command)

    # sys.stderr.write("ret: {}\n".format(ret))

    sed_command = (
        """bash -c 'sed -i '"'"'s/main.*.chunk.js/main.invalid.chunk.js/g'"'"' """
    )
    sed_command += " higlass-app/precache-manifest.*.js'"

    ret = hg_container.exec_run(sed_command)
    # sys.stderr.write("ret: {}\n".format(ret))

    sed_command = """bash -c 'sed -i '"'"'s/index.html/index_invalidated_by_higlass_manage/g'"'"' """
    sed_command += " higlass-app/precache-manifest.*.js'"

    ret = hg_container.exec_run(sed_command)
    # sys.stderr.write("ret: {}\n".format(ret))
    sys.stderr.write("Replaced js file\n")

    sys.stderr.write("Started\n")
