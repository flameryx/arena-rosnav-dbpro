import csv
import math
import time

import numpy as np
import torch
from stable_baselines3.common.vec_env import VecNormalize

import rospy
import visualization_msgs
from visualization_msgs.msg import Marker


class Evaluator:
    def __init__(self):
        self.colors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 1.0, 0.0], [1.0, 0.0, 1.0],
                       [0.0, 1.0, 1.0]]  # TODO This is not optimal as only 6 models can be visualized
        self.agent_visualization = rospy.Publisher('/eval_sim/all_in_one_action_prob_vis', Marker, queue_size=1)

    def evaluate_policy_manually(self, policy: callable, action_probs_func: callable, env: VecNormalize, episodes: int,
                                 log_folder: str, gamma: float,
                                 all_in_config_file: str, log_statistics=True):
        gamma = 0.99
        rewards = np.zeros((episodes,))
        global_path_rewards = np.zeros((episodes,))
        collisions = np.zeros((episodes,))
        distance_travelled = []
        travel_time = []
        is_success = np.zeros((episodes,))
        computation_times = []
        computation_times_local_planner = []

        model_distribution = np.zeros((episodes, env.env_method("get_number_models")[0]))
        model_distribution_close_obst_dist = np.zeros((episodes, env.env_method("get_number_models")[0]))
        model_distribution_medium_obst_dist = np.zeros((episodes, env.env_method("get_number_models")[0]))
        model_distribution_large_obst_dist = np.zeros((episodes, env.env_method("get_number_models")[0]))

        policy_switching_prob_close_dist = np.zeros((episodes,))
        close_dist_iterations = 0
        policy_switching_prob_medium_dist = np.zeros((episodes,))
        medium_dist_iterations = 0
        policy_switching_prob_large_dist = np.zeros((episodes,))
        large_dist_iterations = 0
        policy_switching_prob_avg = np.zeros((episodes,))
        overall_iterations = 0

        model_names = env.env_method('get_model_names')[0]

        env.reset()

        # run evaluation
        for i in range(episodes):
            print('Episode {:d} / {:d}'.format(i, episodes))
            obs = env.reset()[0]
            done = False
            current_reward = 0
            current_iteration = 0
            while not done:
                if log_statistics:
                    start_time = time.time()
                action = np.array([policy(obs)])
                obs_forward = torch.as_tensor([obs]).to("cuda")
                possible_actions = [i for i in range(env.env_method("get_number_models")[0])]
                possible_actions_tensor = torch.as_tensor(possible_actions).to("cuda")
                with torch.no_grad():
                    action_probs_tensor = action_probs_func(obs_forward, possible_actions_tensor)
                if torch.is_tensor(action_probs_tensor):
                    action_probs = action_probs_tensor.cpu().numpy()
                else:
                    action_probs = np.array(action_probs_tensor)
                self.visualize_action_probs(action_probs, model_names)

                # time.sleep(0.05)
                obs, reward, done, info = env.step(action)

                if log_statistics:
                    end_time = time.time()
                    comp_time_in_ms = (end_time - start_time) * 1000.0
                    computation_times.append(comp_time_in_ms)

                info = info[0]
                done = done[0]
                reward = env.get_original_reward()[0]
                obs = obs[0]

                if log_statistics:
                    computation_times_local_planner.append(info['local_planner_comp_time'])

                    current_reward += reward * (gamma ** current_iteration)
                    current_iteration += 1
                    if done:
                        rewards[i] = current_reward
                        global_path_rewards[i] = info['global_path_reward']
                        collisions[i] = info['collisions']
                        is_success[i] = info['is_success']
                        if is_success[i] == 1:
                            distance_travelled.append(info['distance_travelled'])
                            travel_time.append(info['time'])

                        overall_iterations += info['action_iterations']
                        close_dist_iterations += info['action_iterations_close_obst_dist']
                        medium_dist_iterations += info['action_iterations_medium_obst_dist']
                        large_dist_iterations += info['action_iterations_large_obst_dist']

                        model_distribution[i, :] = info['model_distribution'] * info['action_iterations']
                        model_distribution_close_obst_dist[i, :] = info['model_distribution_close_obst_dist'] * info[
                            'action_iterations_close_obst_dist']
                        model_distribution_medium_obst_dist[i, :] = info['model_distribution_medium_obst_dist'] * info[
                            'action_iterations_medium_obst_dist']
                        model_distribution_large_obst_dist[i, :] = info['model_distribution_large_obst_dist'] * info[
                            'action_iterations_large_obst_dist']

                        policy_switching_prob_avg[i] = info['action_change_prob'] * info['action_iterations']
                        policy_switching_prob_close_dist[i] = info['action_change_prob_close_obst_dist'] * info[
                            'action_iterations_close_obst_dist']
                        policy_switching_prob_medium_dist[i] = info['action_change_prob_medium_obst_dist'] * info[
                            'action_iterations_medium_obst_dist']
                        policy_switching_prob_large_dist[i] = info['action_change_prob_large_obst_dist'] * info[
                            'action_iterations_large_obst_dist']

        if log_statistics:
            # remove empty entries
            model_distribution_close_obst_dist = [i for i in model_distribution_close_obst_dist if np.sum(i) != 0]
            model_distribution_medium_obst_dist = [i for i in model_distribution_medium_obst_dist if np.sum(i) != 0]
            model_distribution_large_obst_dist = [i for i in model_distribution_large_obst_dist if np.sum(i) != 0]

            comp_times_mean_per_second = np.mean(computation_times) * (
                    10.0 / env.env_method("get_all_in_one_planner_frequency")[0])
            # save results
            with open(log_folder + '/evaluation_full.csv', 'w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(["rewards", "collisions", "is_success"])
                for i in range(episodes - 1):
                    writer.writerow(["{:.2f}".format(rewards[i]), collisions[i], is_success[i]])

            mean_model_distribution = np.sum(model_distribution, axis=0) / overall_iterations
            mean_model_distribution_close_dist = np.sum(model_distribution_close_obst_dist,
                                                        axis=0) / close_dist_iterations
            mean_model_distribution_medium_dist = np.sum(model_distribution_medium_obst_dist,
                                                         axis=0) / medium_dist_iterations
            mean_model_distribution_large_dist = np.sum(model_distribution_large_obst_dist,
                                                        axis=0) / large_dist_iterations

            mean_policy_switching_prob_avg = np.sum(policy_switching_prob_avg, axis=0) / overall_iterations
            mean_policy_switching_prob_close_dist = np.sum(policy_switching_prob_close_dist,
                                                           axis=0) / close_dist_iterations
            mean_policy_switching_prob_medium_dist = np.sum(policy_switching_prob_medium_dist,
                                                            axis=0) / medium_dist_iterations
            mean_policy_switching_prob_large_dist = np.sum(policy_switching_prob_large_dist,
                                                           axis=0) / large_dist_iterations

            mean_time_out = 1 - np.mean(collisions) - np.mean(is_success)

            with open(log_folder + "/evaluation_summary.txt", 'w') as file:
                file.write("Mean reward: " + str(np.mean(rewards)) + "\n")
                file.write("Mean global path reward: " + str(np.mean(global_path_rewards)) + "\n")
                file.write("Mean collisions: " + str(np.mean(collisions)) + "\n")
                file.write("Mean distance travelled: " + str(np.mean(distance_travelled)) + "\n")
                file.write("Mean time: " + str(np.mean(travel_time)) + "\n")
                file.write("Mean success rate: " + str(np.mean(is_success)) + "\n")
                file.write("Mean time out: " + str(mean_time_out) + "\n")
                file.write("Mean computation time per second simulation time " + str(comp_times_mean_per_second) + "\n")
                file.write("Mean computation per local planner iteration " + str(
                    np.mean(computation_times_local_planner)) + "\n")
                file.write("Mean model distribution: " + str(mean_model_distribution) + "\n")
                file.write("Mean model distribution close obstacle distance: " + str(
                    mean_model_distribution_close_dist) + "\n")
                file.write("Mean model distribution medium obstacle distance: " + str(
                    mean_model_distribution_medium_dist) + "\n")
                file.write("Mean model distribution large obstacle distance: " + str(
                    mean_model_distribution_large_dist) + "\n")

                file.write("Mean policy switching prob: " + str(mean_policy_switching_prob_avg) + "\n")
                file.write("Mean policy switching prob close obstacle distance: " + str(
                    mean_policy_switching_prob_close_dist) + "\n")
                file.write("Mean policy switching prob medium obstacle distance: " + str(
                    mean_policy_switching_prob_medium_dist) + "\n")
                file.write("Mean policy switching prob large obstacle distance: " + str(
                    mean_policy_switching_prob_large_dist) + "\n")

                file.write("With models: " + str(env.env_method("get_model_names")[0]))

            summary_csv = [str(np.mean(is_success)), str(np.mean(collisions)), str(mean_time_out),
                           str(np.mean(travel_time)), str(np.mean(distance_travelled)), str(np.mean(rewards)),
                           str(comp_times_mean_per_second), str(np.mean(computation_times_local_planner)),
                           str(mean_model_distribution), str(mean_model_distribution_close_dist),
                           str(mean_model_distribution_medium_dist), str(mean_model_distribution_large_dist),
                           str(mean_policy_switching_prob_avg), str(mean_policy_switching_prob_close_dist),
                           str(mean_policy_switching_prob_medium_dist), str(mean_policy_switching_prob_large_dist)]
            with open(log_folder + '/evaluation_summary.csv', 'w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(["Mean success rate", summary_csv[0]])
                writer.writerow(["Mean collisions", summary_csv[1]])
                writer.writerow(["Mean time outs", summary_csv[2]])
                writer.writerow(["Mean time", summary_csv[3]])
                writer.writerow(["Mean distance travelled", summary_csv[4]])
                writer.writerow(["Mean reward", summary_csv[5]])
                writer.writerow(["Mean computation time per second simulation time", summary_csv[6]])
                writer.writerow(["Mean computation per local planner iteration", summary_csv[7]])
                writer.writerow(["Mean model distribution", summary_csv[8]])
                writer.writerow(["Mean model distribution close obstacle distance", summary_csv[9]])
                writer.writerow(["Mean model distribution medium obstacle distance", summary_csv[10]])
                writer.writerow(["Mean model distribution large obstacle distance", summary_csv[11]])
                writer.writerow(["Mean policy switching prob", summary_csv[12]])
                writer.writerow(["Mean policy switching prob close obstacle distance", summary_csv[13]])
                writer.writerow(["Mean policy switching prob medium obstacle distance", summary_csv[14]])
                writer.writerow(["Mean policy switching prob large obstacle distance", summary_csv[15]])

            return summary_csv

    def visualize_action_probs(self, action_probs: [float], names: [str]):
        for i in range(action_probs.size):
            marker = Marker()
            marker.header.stamp = rospy.get_rostime()
            marker.header.frame_id = 'map'
            marker.id = 200 + i

            marker.type = visualization_msgs.msg.Marker.TEXT_VIEW_FACING
            marker.action = visualization_msgs.msg.Marker.ADD

            marker.color.r = self.colors[i][0]
            marker.color.g = self.colors[i][1]
            marker.color.b = self.colors[i][2]
            marker.color.a = 1

            marker.pose.position.x = i + 1
            marker.pose.position.y = -3

            marker.scale.z = 1

            marker.text = names[i] + ": " + "{:.2f}".format((math.e ** action_probs[i]) * 100) + "%"

            self.agent_visualization.publish(marker)
