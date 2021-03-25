import os, sys
import rospy
import time
import rosnode
from typing import Union
from datetime import datetime as dt
import warnings

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import SubprocVecEnv, DummyVecEnv, VecNormalize
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.callbacks import EvalCallback, StopTrainingOnRewardThreshold
from stable_baselines3.common.utils import set_random_seed

from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.scripts.custom_policy import *
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.rl_agent.envs.flatland_gym_env import FlatlandEnv
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.tools.argsparser import parse_training_args
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.tools.train_agent_utils import *
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.tools.custom_mlp_utils import *
from arena_navigation.arena_local_planner.learning_based.arena_local_planner_drl.tools.staged_train_callback import InitiateNewTrainStage


def get_agent_name(args) -> str:
    """ Function to get agent name to save to/load from file system
    
    Example names:
    "MLP_B_64-64_P_32-32_V_32-32_relu_2021_01_07__10_32"
    "DRL_LOCAL_PLANNER_2021_01_08__7_14"

    :param args (argparse.Namespace): Object containing the program arguments
    """
    START_TIME = dt.now().strftime("%Y_%m_%d__%H_%M")

    if args.custom_mlp:
        return (
            "MLP_B_" + args.body 
            + "_P_" + args.pi 
            + "_V_" + args.vf + "_" 
            + args.act_fn + "_" + START_TIME)
    if args.load is None:
        return args.agent + "_" + START_TIME
    return args.load

def get_paths(agent_name: str, args) -> dict:
    """ 
    Function to generate agent specific paths 
    
    :param agent_name: Precise agent name (as generated by get_agent_name())
    :param args (argparse.Namespace): Object containing the program arguments
    """
    dir = rospkg.RosPack().get_path('arena_local_planner_drl')

    PATHS = {
        'model': 
            os.path.join(
                dir, 'agents', agent_name),
        'tb': 
            os.path.join(
                dir, 'training_logs', 'tensorboard', agent_name),
        'eval': 
            os.path.join(
                dir, 'training_logs', 'train_eval_log', agent_name),
        'robot_setting': 
            os.path.join(
                rospkg.RosPack().get_path('simulator_setup'),
                'robot', 'myrobot' + '.model.yaml'),
        'hyperparams':
            os.path.join(
                dir, 'configs', 'hyperparameters'),
        'robot_as': 
            os.path.join(
                dir, 'configs', 'default_settings.yaml'),
        'curriculum': 
            os.path.join(
                dir, 'configs', 'training_curriculum_map1small.yaml')
    }
    # check for mode
    if args.load is None:
        os.makedirs(PATHS.get('model'))
    else:
        if (not os.path.isfile(
                os.path.join(PATHS.get('model'), AGENT_NAME + ".zip")) 
            and not os.path.isfile(
                os.path.join(PATHS.get('model'), "best_model.zip"))
            ):
            raise FileNotFoundError(
                "Couldn't find model named %s.zip' or 'best_model.zip' in '%s'" 
                % (AGENT_NAME, PATHS.get('model')))
    # evaluation log enabled
    if args.eval_log:
        if not os.path.exists(PATHS.get('eval')):
            os.makedirs(PATHS.get('eval'))
    else:
        PATHS['eval'] = None
    # tensorboard log enabled
    if args.tb:
        if not os.path.exists(PATHS.get('tb')):
            os.makedirs(PATHS.get('tb'))
    else:
        PATHS['tb'] = None

    return PATHS

def make_envs(rank: int, 
              params: dict, 
              seed: int=0, 
              PATHS: dict=None, 
              train: bool=True):
    """
    Utility function for multiprocessed env
    
    :param rank: (int) index of the subprocess
    :param params: (dict) hyperparameters of agent to be trained
    :param seed: (int) the inital seed for RNG
    :param PATHS: (dict) script relevant paths
    :param train: (bool) to differentiate between train and eval env
    :param args: (Namespace) program arguments
    :return: (Callable)
    """
    def _init() -> Union[gym.Env, gym.Wrapper]:
        if train:
            # train env
            env = FlatlandEnv(
                f"sim_{rank+1}", 
                PATHS.get('robot_setting'), PATHS.get('robot_as'), 
                params['reward_fnc'], params['discrete_action_space'], 
                goal_radius=params['goal_radius'], 
                max_steps_per_episode=params['train_max_steps_per_episode'],
                debug=args.debug, 
                task_mode=params['task_mode'], curr_stage=params['curr_stage'], 
                PATHS=PATHS)
        else:
            # eval env
            env = Monitor(
                FlatlandEnv(
                    f"eval_sim",
                    PATHS.get('robot_setting'), PATHS.get('robot_as'), 
                    params['reward_fnc'], params['discrete_action_space'], 
                    goal_radius=params['goal_radius'], 
                    max_steps_per_episode=params['eval_max_steps_per_episode'], 
                    train_mode=False, debug=args.debug, 
                    task_mode=params['task_mode'], curr_stage=params['curr_stage'],
                    PATHS=PATHS
                    ),
                PATHS.get('eval'), info_keywords=("done_reason", "is_success"))
        env.seed(seed + rank)
        return env
    set_random_seed(seed)
    return _init

def wait_for_nodes(n_envs: int, timeout: int=30, nodes_per_ns: int=3):
    """
    Checks for timeout seconds if all nodes to corresponding namespace are online.
    
    :param n_envs: (int) number of virtual environments
    :param timeout: (int) seconds to wait for each ns
    :param nodes_per_ns: (int) usual number of nodes per ns 
    """
    for i in range(n_envs):
        for k in range(timeout):
            ns = rosnode.get_node_names(namespace='sim_'+str(i+1))

            if len(ns) < nodes_per_ns:
                warnings.warn(f"Check if all simulation parts of namespace '{'/sim_'+str(i+1)}' are running properly")
                warnings.warn(f"Trying to connect again..")
            else:
                break

            assert (k < timeout-1
            ), f"Timeout while trying to connect to nodes of '{'/sim_'+str(i+1)}'"

            time.sleep(1)

if __name__ == "__main__":
    args, _ = parse_training_args()

    if args.debug:
        rospy.init_node("debug_node", disable_signals=False)
        
    # generate agent name and model specific paths
    AGENT_NAME = get_agent_name(args)
    PATHS = get_paths(AGENT_NAME, args)

    print("________ STARTING TRAINING WITH:  %s ________\n" % AGENT_NAME)

    # check if simulations are booted
    wait_for_nodes(n_envs=args.n_envs, timeout=5)
        
    # initialize hyperparameters (save to/ load from json)
    params = initialize_hyperparameters(
        PATHS=PATHS, load_target=args.load, config_name=args.config, n_envs=args.n_envs)

    # instantiate train environment
    # when debug run on one process only
    if not args.debug:
        env = SubprocVecEnv(
            [make_envs(i, params=params, PATHS=PATHS) 
                for i in range(args.n_envs)], 
            start_method='fork')
    else:
        env = DummyVecEnv(
            [make_envs(i, params=params, PATHS=PATHS) 
                for i in range(args.n_envs)])

    # threshold settings for training curriculum
    # type can be either 'succ' or 'rew'
    trainstage_cb = InitiateNewTrainStage(
        n_envs=args.n_envs,
        treshhold_type="succ", 
        upper_threshold=0.9, lower_threshold=0.6, 
        task_mode=params['task_mode'], verbose=1)
    
    # stop training on reward threshold callback
    stoptraining_cb = StopTrainingOnRewardThreshold(
        reward_threshold=15, verbose=1)

    # instantiate eval environment
    # take task_manager from first sim (currently evaluation only provided for single process)
    eval_env = DummyVecEnv(
        [make_envs(0, params=params, PATHS=PATHS, train=False)])

    # try to load most recent vec_normalize obj (contains statistics like moving avg)
    if params['normalize']:
        load_path = os.path.join(PATHS['model'], 'vec_normalize.pkl')
        if os.path.isfile(load_path):
            env = VecNormalize.load(
                load_path=load_path, venv=env)
            eval_env = VecNormalize.load(
                load_path=load_path, venv=eval_env)
            print("Succesfully loaded VecNormalize object from pickle file..")
        else:
            env = VecNormalize(
                env, training=True, 
                norm_obs=True, norm_reward=False, clip_reward=15)
            eval_env = VecNormalize(
                eval_env, training=True, 
                norm_obs=True, norm_reward=False, clip_reward=15)
    
    # evaluation settings
    # n_eval_episodes: number of episodes to evaluate agent on
    # eval_freq: evaluate the agent every eval_freq train timesteps
    eval_cb = EvalCallback(
        eval_env=eval_env,          train_env=env,
        n_eval_episodes=30,         eval_freq=15000, 
        log_path=PATHS.get('eval'), best_model_save_path=PATHS.get('model'), 
        deterministic=True,         callback_on_eval_end=trainstage_cb,
        callback_on_new_best=stoptraining_cb)
   
    # determine mode
    if args.custom_mlp:
        # custom mlp flag
        model = PPO(
            "MlpPolicy", env, 
            policy_kwargs = dict(
                net_arch = args.net_arch, activation_fn = get_act_fn(args.act_fn)), 
            gamma = params['gamma'],            n_steps = params['n_steps'], 
            ent_coef = params['ent_coef'],      learning_rate = params['learning_rate'], 
            vf_coef = params['vf_coef'],        max_grad_norm = params['max_grad_norm'], 
            gae_lambda = params['gae_lambda'],  batch_size = params['m_batch_size'], 
            n_epochs = params['n_epochs'],      clip_range = params['clip_range'], 
            tensorboard_log = PATHS.get('tb'),  verbose = 1
        )
    elif args.agent is not None:
        # predefined agent flag
        if args.agent == "MLP_ARENA2D":
                model = PPO(
                    MLP_ARENA2D_POLICY, env, 
                    gamma = params['gamma'],            n_steps = params['n_steps'], 
                    ent_coef = params['ent_coef'],      learning_rate = params['learning_rate'], 
                    vf_coef = params['vf_coef'],        max_grad_norm = params['max_grad_norm'], 
                    gae_lambda = params['gae_lambda'],  batch_size = params['m_batch_size'], 
                    n_epochs = params['n_epochs'],      clip_range = params['clip_range'], 
                    tensorboard_log = PATHS.get('tb'),  verbose = 1
                )

        elif args.agent == "DRL_LOCAL_PLANNER" or args.agent == "CNN_NAVREP":
            if args.agent == "DRL_LOCAL_PLANNER":
                policy_kwargs = policy_kwargs_drl_local_planner
            else:
                policy_kwargs = policy_kwargs_navrep

            model = PPO(
                "CnnPolicy", env, 
                policy_kwargs = policy_kwargs, 
                gamma = params['gamma'],            n_steps = params['n_steps'], 
                ent_coef = params['ent_coef'],      learning_rate = params['learning_rate'], 
                vf_coef = params['vf_coef'],        max_grad_norm = params['max_grad_norm'], 
                gae_lambda = params['gae_lambda'],  batch_size = params['m_batch_size'], 
                n_epochs = params['n_epochs'],      clip_range = params['clip_range'], 
                tensorboard_log = PATHS.get('tb'),  verbose = 1
            )
    else:
        # load flag
        if os.path.isfile(
                os.path.join(PATHS.get('model'), AGENT_NAME + ".zip")):
            model = PPO.load(
                os.path.join(PATHS.get('model'), AGENT_NAME), env)
        elif os.path.isfile(
                os.path.join(PATHS.get('model'), "best_model.zip")):
            model = PPO.load(
                os.path.join(PATHS.get('model'), "best_model"), env)
        update_hyperparam_model(model, PATHS, params, args.n_envs)

    # set num of timesteps to be generated
    if args.n is None:
        n_timesteps = 40000000
    else:
        n_timesteps = args.n

    # start training
    start = time.time()
    model.learn(
        total_timesteps = n_timesteps, callback=eval_cb, reset_num_timesteps=True)
    print(f'Time passed: {time.time()-start}s')

    # update the timesteps the model has trained in total
    # update_total_timesteps_json(n_timesteps, PATHS)
    print("training done!")
    sys.exit()
    
