from tensorflow import keras
import tensorflow as tf
from collections import deque
from .utils import Utils
import numpy as np
import random
from dqn_msg.srv import Dqnn
from std_srvs.srv import Empty
import psutil
import time
import rclpy
import os
from copy import copy

from rclpy.node import Node
physical_devices = tf.config.list_physical_devices('GPU')
try:
    tf.config.experimental.set_memory_growth(physical_devices[0], True)
except:
    # Invalid device or cannot modify virtual devices once initialized.
    pass

loss_function = keras.losses.Huber()


class ACNetwork():
    def __init__(self, name, model_load=False, ep=0) -> None:
        self.name = name
        self.dir_path = os.path.dirname(os.path.realpath(__file__))
        self.upper_bound=1.5  
        self.lower_bound=-1.5 
        self.critic_lr = 0.002
        self.actor_lr = 0.001
        self.critic_optimizer = tf.keras.optimizers.Adam(self.critic_lr)
        self.actor_optimizer = tf.keras.optimizers.Adam(self.actor_lr)
        self.optimizer = keras.optimizers.Adam(learning_rate=0.001)
        if (model_load):
            self.ep = ep
            self.load_data()
          
        else:
            #self.model = self.create_model()
            self.actor_model = self.create_actor_model()
            self.critic_model = self.create_critic_model()

            self.target_actor = self.create_actor_model()
            self.target_critic = self.create_critic_model()

            self.epsilon = 1
            self.replay_memory = deque(maxlen=100_000)
            self.ep = ep
            
        ep_rewards=[]  
       
        #learning_rate=0.00025
        
        # to note we are having the same target of if we load the model
      
        self.actions = [-np.pi/2, -np.pi/4, 0, np.pi/4, np.pi/2]
        self.actions_size = 5
        self.state_size = 3
        self.discout_factor = 0.99
        self.minbatch_size = 64
        self.MIN_REPLAY_MEMORY_SIZE = 64
      

       

        self.target_update_counter = 0

    def create_actor_model(self):
        # Initialize weights between -3e-3 and 3-e3
        last_init = tf.random_uniform_initializer(minval=-0.003, maxval=0.003)

        inputs = keras.layers.Input(shape=(3,))
        out = keras.layers.Dense(256, activation="relu",kernel_initializer='lecun_uniform')(inputs)
        dropout=keras.layers.Dropout(0.5)(out)
        out = keras.layers.Dense(256, activation="relu",kernel_initializer='lecun_uniform')(dropout)
        outputs = keras.layers.Dense(1, activation="tanh",kernel_initializer= last_init)(out)

        # Our upper bound is 2.0 for Pendulum.
        outputs = outputs * self.upper_bound
        model = tf.keras.Model(inputs, outputs)
        return model
    
    def create_critic_model(self):
        last_init = tf.random_uniform_initializer(minval=-0.003, maxval=0.003)
        state_input = keras.layers.Input(shape=(3,))
        state_out = keras.layers.Dense(256, activation="relu",kernel_initializer='lecun_uniform')(state_input)
        state_out = keras.layers.Dense(256, activation="relu",kernel_initializer='lecun_uniform')(state_out)

            # Action as input
        action_input = keras.layers.Input(shape=(1,))
        action_out = keras.layers.Dense(256, activation="relu",kernel_initializer='lecun_uniform')(action_input)

            # Both are passed through seperate layer before concatenating
        concat = keras.layers.Concatenate()([state_out, action_out])

        out = keras.layers.Dense(256, activation="relu",kernel_initializer='lecun_uniform')(concat)
        out = keras.layers.Dense(256, activation="relu",kernel_initializer='lecun_uniform')(out)
        outputs = keras.layers.Dense(1,kernel_initializer='lecun_uniform')(out)

        # Outputs single value for give state-action
        model = tf.keras.Model([state_input, action_input], outputs)

        return model

    def policy(self,state, noise_object):
        sampled_actions = tf.squeeze(self.actor_model(state))
        noise = noise_object()
        # Adding noise to action
        sampled_actions = sampled_actions.numpy() + noise
         
        # We make sure action is within bounds
        legal_action = np.clip(sampled_actions, self.lower_bound, self.upper_bound)
        print("state",state)
        print("action",legal_action)
        return [np.squeeze(legal_action)]
    @tf.function
    def update(
        self, state_batch, action_batch, reward_batch, next_state_batch,
    ):
            # Training and updating Actor & Critic networks.
            # See Pseudo Code.
            with tf.GradientTape() as tape:
                target_actions = self.target_actor(next_state_batch, training=True)
                
                y = reward_batch + self.discout_factor * self.target_critic(
                    [next_state_batch, target_actions], training=True
                )
                critic_value = self.critic_model([state_batch, action_batch], training=True)
                critic_loss = tf.math.reduce_mean(tf.math.square(y - critic_value))

            critic_grad = tape.gradient(critic_loss, self.critic_model.trainable_variables)
            self.critic_optimizer.apply_gradients(
                zip(critic_grad, self.critic_model.trainable_variables)
            )

            with tf.GradientTape() as tape:
                actions = self.actor_model(state_batch, training=True)
                critic_value = self.critic_model([state_batch, actions], training=True)
                # Used `-value` as we want to maximize the value given
                # by the critic for our actions
                actor_loss = -tf.math.reduce_mean(critic_value)

            actor_grad = tape.gradient(actor_loss, self.actor_model.trainable_variables)
            self.actor_optimizer.apply_gradients(
                zip(actor_grad, self.actor_model.trainable_variables)
            )

    @tf.function
    def update_target(self,target_weights, weights, tau):
     for (a, b) in zip(target_weights, weights):
        a.assign(b * tau + a * (1 - tau))

    def update_replay_buffer(self, sample):
        self.replay_memory.append(sample)

    def learn(self):
        if (self.MIN_REPLAY_MEMORY_SIZE > len(self.replay_memory)):
            return
    
        minibatch = random.sample(self.replay_memory, self.minbatch_size)

        state_batch = tf.convert_to_tensor([batch[0] for batch in minibatch])
        reward_batch = tf.convert_to_tensor([batch[1] for batch in minibatch])
        action_batch = tf.convert_to_tensor([batch[2] for batch in minibatch])
        next_state_batch = tf.convert_to_tensor([batch[3] for batch in minibatch])
        
       

        self.update(state_batch, action_batch, reward_batch, next_state_batch)

    def load_data(self):
        self.actor_model = self.create_actor_model()
        self.target_actor = self.create_actor_model()
        self.critic_model = self.create_critic_model()
        self.target_critic = self.create_critic_model()
        path1 = os.path.join(self.dir_path, self.get_model_file_name("h5","actor"))
        path2 = os.path.join(self.dir_path, self.get_model_file_name("h5","target-actor"))
        path3 = os.path.join(self.dir_path, self.get_model_file_name("h5","critic"))
        path4 = os.path.join(self.dir_path, self.get_model_file_name("h5","target-critic"))
        self.actor_model = Utils.load_model(self.actor_model, path1)
        self.target_actor = Utils.load_model(self.target_actor, path2)
        self.critic_model = Utils.load_model(self.critic_model, path3)
        self.target_critic = Utils.load_model(self.target_critic, path4)

    
        path = os.path.join(self.dir_path, self.get_model_file_name("obj"))
        self.replay_memory = Utils.load_pickle(path)

    def get_epsilon(self):
        return self.epsilon

    def save_data(self, ep,reward):
        self.ep = ep
        path1 = os.path.join(self.dir_path, self.get_model_file_name("h5","actor"))
        path2 = os.path.join(self.dir_path, self.get_model_file_name("h5","target-actor"))
        path3 = os.path.join(self.dir_path, self.get_model_file_name("h5","critic"))
        path4 = os.path.join(self.dir_path, self.get_model_file_name("h5","target-critic"))
        actor = copy(self.actor_model)
        target_actor = copy(self.target_actor)
        critic = copy(self.critic_model)
        target_critic = copy(self.target_critic)
        actor.compile(optimizer=self.actor_optimizer, loss=loss_function)
        target_actor.compile(optimizer=self.actor_optimizer, loss=loss_function)
        critic.compile(optimizer=self.critic_optimizer, loss=loss_function)
        target_critic.compile(optimizer=self.critic_optimizer, loss=loss_function)
        Utils.save_model(actor, path1)
        Utils.save_model(target_actor, path2)
        Utils.save_model(critic, path3)
        Utils.save_model(target_critic, path4)
        path = os.path.join(self.dir_path, self.get_model_file_name("json"))
        data = {"reward":reward}
        Utils.save_json(path, data)
       
        path = os.path.join(self.dir_path, self.get_model_file_name("obj"))
        Utils.save_pickle(path, self.replay_memory)

    def get_model_file_name(self, type,ext=None):
        return f"models-{self.name}/my-model-{self.ep}-{ext}.{type}"
      




