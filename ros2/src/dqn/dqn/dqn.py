import rclpy

from rclpy.node import Node

import gc
import numpy as np

import tensorflow as tf
from tensorflow import keras
from dqn_msg.srv import Dqnn
from std_srvs.srv import Empty
import psutil
import time

from collections import deque
from .utils import Utils
import os
from keras.callbacks import TensorBoard
import random



AGGREGATE_STATS_EVERY = 5


class ModifiedTensorBoard(TensorBoard):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.step = 1
        self.writer = tf.summary.create_file_writer(self.log_dir)
        self._log_write_dir = self.log_dir

    def set_model(self, model):
        self.model = model

        self._train_dir = os.path.join(self._log_write_dir, 'train')
        self._train_step = self.model._train_counter

        self._val_dir = os.path.join(self._log_write_dir, 'validation')
        self._val_step = self.model._test_counter

        self._should_write_train_graph = False

    def on_epoch_end(self, epoch, logs=None):
        self.update_stats(**logs)

    def on_batch_end(self, batch, logs=None):
        pass

    def on_train_end(self, _):
        pass

    def update_stats(self, **stats):
        with self.writer.as_default():
            for key, value in stats.items():
                tf.summary.scalar(key, value, step=self.step)
                self.writer.flush()


MODEL_NAME = '2x256'


class Dqn(Node):

    def __init__(self):
        super().__init__('dqn')
        self.dir_path = os.path.dirname(os.path.realpath(__file__))
        self.model_load = True
        if (self.model_load):
            self.ep =250
            self.load_data()

        else:
            self.model = self.create_model()
            self.epsilon = 1
            self.replay_memory = deque(maxlen=50_000)
            self.ep = 0

        self.target_model = self.model
        self.actions = [-np.pi/2, -np.pi/4, 0, np.pi/4, np.pi/2]
        self.actions_size = 5
        self.state_size = 3
        self.discout_factor = 0.99
        self.minbatch_size = 64
        self.episode_length = 10_000
        self.steps_per_episode = 500

        self.episode_size = 3000
        self.target_update_counter = 0

        #self.EPSILON_DECAY = 0.992
        self.EPSILON_DECAY = 0.99
        self.MIN_EPSILON = 0.05
        self.MIN_REPLAY_MEMORY_SIZE = 1000
        self.env_result_client = self.create_client(Dqnn, "env_result")
        self.reset_sim_client = self.create_client(Empty, "reset_sim")
        self.stop = False
        self.tensorboard = ModifiedTensorBoard(
            log_dir="logs/{}-{}".format(MODEL_NAME, int(time.time())))

        self.start()
        

    def load_data(self):
        model = self.create_model()
        path = os.path.join(self.dir_path, self.get_model_file_name("h5"))
        self.model = Utils.load_model(model, path)

        path = os.path.join(self.dir_path, self.get_model_file_name("json"))
        self.epsilon = Utils.load_json(path, "epsilon")
        path = os.path.join(self.dir_path, self.get_model_file_name("obj"))
        self.replay_memory = Utils.load_pickle(path)

    def save_data(self):
        path = os.path.join(self.dir_path, self.get_model_file_name("h5"))
        Utils.save_model(self.model, path)
        path = os.path.join(self.dir_path, self.get_model_file_name("json"))
        data = {"epsilon": self.epsilon}
        Utils.save_json(path, data)
        path = os.path.join(self.dir_path, self.get_model_file_name("obj"))
        Utils.save_pickle(path, self.replay_memory)

    def get_model_file_name(self, type):
        return f"models/my-model-{self.ep}.{type}"

    


    def train(self, terminal_state):
        if (self.MIN_REPLAY_MEMORY_SIZE > len(self.replay_memory)):
            return

        minibatch = random.sample(self.replay_memory, self.minbatch_size)

        current_states = np.array([batch[0] for batch in minibatch])
        next_states = np.array([batch[3] for batch in minibatch])
        q_values = self.model.predict(current_states, verbose=0)
        next_q_values = self.target_model.predict(next_states, verbose=0)

        X = []
        y = []
        for index, (current_state, reward, action, next_state, done) in enumerate(minibatch):

            if not done:
                new_q = float(reward+self.discout_factor*np.max(next_q_values[index]))
            else:
                new_q = reward
            
            q_values_y = q_values[index].copy()
            q_values_y[action] = new_q
           
            X.append(current_state)
            y.append(q_values_y)

        self.model.fit(np.array(X), np.array(y), batch_size=self.minbatch_size, verbose=0,
                       shuffle=False)
        if terminal_state:
            self.target_update_counter += 1

        # If counter reaches set value, update target network with weights of main network
        if self.target_update_counter > 5:
            self.target_model.set_weights(self.model.get_weights())
            self.target_update_counter = 0


def main(args=None):
    rclpy.init(args=args)

    dqn = Dqn()

    rclpy.spin(dqn)

    # Destroy the node explicitly
    # (optional - otherwise it will be done automatically
    # when the garbage collector destroys the node object)
    dqn.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()