import numpy as np
import tensorflow as tf

import gym

env = gym.make('CartPole-v0')


t2 = [0.,1.,2.,3.]
print(np.vstack(t2))
t3 = [y for y in t2][:-1]
print(t3)
print(t2[:-1])

# Hyperparameters
H = 8  # Number of hidden layer neurons
learning_rate = 1e-2
gamma = 0.99  # Discount factor for reward
decay_rate = 0.99  # Decay factor for RMSProp leaky sum of grad^2
resume = False  # Resume from previous checkpoint?

model_bs = 3  # Batch size when learning from model
real_bs = 3   # Batch size when learning from real environment

# Model initialization
D = 4  # Input dimensionality

'''
------------------------------------------------------------------------------
Policy Network
'''
# Use observations as input to generate predicted actions
observations_ph = tf.placeholder(tf.float32, [None,4] , name="input_x")
W1 = tf.get_variable("W1", shape=[4, H], initializer=tf.contrib.layers.xavier_initializer())
layer1 = tf.nn.relu(tf.matmul(observations_ph, W1))
W2 = tf.get_variable("W2", shape=[H, 1], initializer=tf.contrib.layers.xavier_initializer())
score = tf.matmul(layer1, W2)
action_sigmoid = tf.nn.sigmoid(score)

training_variables = tf.trainable_variables()
actions_ph = tf.placeholder(tf.float32, [None,1], name="actions_ph")
rewards_ph = tf.placeholder(tf.float32, name="rewards_ph")

log_likelihood = tf.log(actions_ph * (actions_ph-action_sigmoid) + (1-actions_ph) * (actions_ph+action_sigmoid))
loss = -tf.reduce_mean(log_likelihood * rewards_ph)

new_grads = tf.gradients(loss, training_variables)  # Calculate gradients to tvars wrt loss

w1_grad_ph = tf.placeholder(tf.float32, name="w1_grad_ph")
w2_grad_ph = tf.placeholder(tf.float32, name="w2_grad_ph")
w1w2_list = [w1_grad_ph, w2_grad_ph]
optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
update_grads = optimizer.apply_gradients(zip(w1w2_list, training_variables))

'''
------------------------------------------------------------------------------
Model Network
Here we implement a multi-layer neural network that predicts the next observation,
reward, and done state from a current state and action.
'''
mH = 256  # Model layer size

previous_state = tf.placeholder(tf.float32, [None,5] , name="previous_state")
W1M = tf.get_variable("W1M", shape=[5, mH], initializer=tf.contrib.layers.xavier_initializer())
B1M = tf.Variable(tf.zeros([mH]), name="B1M")
layer1M = tf.nn.relu(tf.matmul(previous_state, W1M) + B1M)

W2M = tf.get_variable("W2M", shape=[mH, mH], initializer=tf.contrib.layers.xavier_initializer())
B2M = tf.Variable(tf.zeros([mH]), name="B2M")
layer2M = tf.nn.relu(tf.matmul(layer1M, W2M) + B2M)

wO = tf.get_variable("wO", shape=[mH, 4], initializer=tf.contrib.layers.xavier_initializer())
wR = tf.get_variable("wR", shape=[mH, 1], initializer=tf.contrib.layers.xavier_initializer())
wD = tf.get_variable("wD", shape=[mH, 1], initializer=tf.contrib.layers.xavier_initializer())

bO = tf.Variable(tf.zeros([4]), name="bO")
bR = tf.Variable(tf.zeros([1]), name="bR")
bD = tf.Variable(tf.ones([1]), name="bD")

predicted_observation = tf.matmul(layer2M, wO, name="predicted_observation") + bO
predicted_reward = tf.matmul(layer2M, wR, name="predicted_reward") + bR
predicted_done = tf.sigmoid(tf.matmul(layer2M, wD, name="predicted_done") + bD)

true_observation = tf.placeholder(tf.float32,[None,4], name="true_observation")
true_reward = tf.placeholder(tf.float32,[None,1], name="true_reward")
true_done = tf.placeholder(tf.float32,[None,1], name="true_done")

predicted_state = tf.concat([predicted_observation, predicted_reward, predicted_done], 1)

observation_loss = tf.square(true_observation - predicted_observation)
reward_loss = tf.square(true_reward - predicted_reward)
done_loss = (predicted_done * true_done) + (1-predicted_done) * (1-true_done)
done_loss = -tf.log(done_loss)

model_loss = tf.reduce_mean(observation_loss + done_loss + reward_loss)

model_adam = tf.train.AdamOptimizer(learning_rate=learning_rate)
update_model = model_adam.minimize(model_loss)


'''
------------------------------------------------------------------------------
Helper Functions
'''
def resetGradBuffer(grad_buffer):
    for ix, grad in enumerate(grad_buffer):
        grad_buffer[ix] = grad * 0
    return grad_buffer


def discount_and_normalize_rewards(r):
    # Take 1D float array of rewards and compute discounted reward
    discounted_r = np.zeros_like(r)
    running_add = 0
    for t in reversed(range(0, r.size)):
        running_add = running_add * gamma + r[t]
        discounted_r[t] = running_add

    discounted_r -= np.mean(discounted_r)
    discounted_r /= np.std(discounted_r)
    return discounted_r.astype(np.float32)


# This function uses our model to produce a new state when given a previous state and action
def stepModel(sess, observation_list, action):
    toFeed = np.reshape(np.hstack([observation_list[-1][0], np.array(action)]), [1, 5])
    myPredict = sess.run([predicted_state], feed_dict={previous_state: toFeed})
    reward = myPredict[0][:, 4]
    observation = myPredict[0][:, 0:4]
    observation[:, 0] = np.clip(observation[:, 0], -2.4, 2.4)
    observation[:, 2] = np.clip(observation[:, 2], -0.4, 0.4)
    doneP = np.clip(myPredict[0][:, 5], 0, 1)
    if doneP > 0.1 or len(observation_list) >= 300:
        done = True
    else:
        done = False
    return observation, reward, done


'''
------------------------------------------------------------------------------
Training the Policy and Model
'''
hist_observation, hist_action, hist_reward, hist_done = [], [], [], []
running_reward = None
reward_sum = 0
episode_number = 1
real_episodes = 1
init = tf.global_variables_initializer()
batch_size = real_bs

drawFromModel = False   # When set to True, will use model for observations
trainTheModel = True    # Whether to train the model
trainThePolicy = False  # Whether to train the policy
switch_point = 1

# Launch the graph
with tf.Session() as sess:
    rendering = False
    sess.run(init)
    observation = env.reset()
    grad_buffer = sess.run(training_variables)
    grad_buffer = resetGradBuffer(grad_buffer)

    while episode_number <= 5000:

        # Start displaying environment once performance is acceptably high.
        if (reward_sum / batch_size > 150 and drawFromModel == False) or rendering == True:
            env.render()
            rendering = True

        observation = np.reshape(observation, [1, 4])
        hist_observation.append(observation)

        action_probability = sess.run(action_sigmoid, feed_dict={observations_ph: observation})
        action = 1 if np.random.uniform() < action_probability else 0
        hist_action.append(action)

        # Step the  model or real environment and get new measurements
        if drawFromModel == False:
            observation, reward, done, info = env.step(action)
        else:
            observation, reward, done = stepModel(sess, hist_observation, action)

        reward_sum += reward

        hist_done.append(int(done))  # True becomes 1, False becomes 0
        hist_reward.append(reward)  # Record reward (has to be done after we call step() to get reward for previous action)

        if done:
            if drawFromModel == False:
                real_episodes += 1

            episode_number += 1

            # Stack together all inputs, hidden states, action gradients, and rewards for this episode
            epo = np.vstack(hist_observation)
            epa = np.vstack(hist_action)
            epr = np.vstack(hist_reward)
            epd = np.vstack(hist_done)
            hist_observation, hist_action, hist_reward, hist_done = [], [], [], []  # Reset array memory

            if trainTheModel == True:
                actions = epa[:-1]          # All actions except the last
                state_prevs1 = epo[:-1, :]  # All states except the last
                # When observing state_prevs[index], we decided to take actions[index]
                state_prevs = np.hstack([state_prevs1, actions])  # So shape [None, 5]
                # When taking actions[index] from state_prevs[index]
                #    1. We reached state_nexts[index]
                state_nexts = epo[1:, :]
                #    2. We collected rewards[index]
                rewards = np.array(epr[1:, :])
                #    3. And dones[index] became either 1 or 0
                dones = np.array(epd[1:, :])
                state_nexts_all = np.hstack([state_nexts, rewards, dones])
                print("Shape of action: {}, state_prevs1: {}, state_prevs: {}".
                      format(np.shape(actions), np.shape(state_prevs1), np.shape(state_prevs)))
                print("    state_nexts: {}, rewards: {}, dones: {}".
                      format(np.shape(state_nexts), np.shape(rewards), np.shape(dones)))

                # Train our model simulator, given
                #    - previous_states
                #    - we use model to predict next states, rewards and dones
                #    - Then update_model calculates and minimizes loss against true states, rewards, dones
                _ = sess.run(update_model, feed_dict={previous_state: state_prevs,
                                                     true_observation: state_nexts,
                                                     true_done: dones,
                                                     true_reward: rewards})

            if trainThePolicy == True:
                discounted_epr = discount_and_normalize_rewards(epr)
                actions = np.array([np.abs(value-1) for value in epa])  # *** WHY INVERT??? ***
                t_grad = sess.run(new_grads,
                                  feed_dict={observations_ph: epo,
                                             actions_ph: actions,
                                             rewards_ph: discounted_epr})

                if np.sum(t_grad[0] == t_grad[0]) == 0:
                    print("Terminating because of grad problem")
                    break
                for idx, grad in enumerate(t_grad):
                    grad_buffer[idx] += grad

            if episode_number > 0 and (episode_number % batch_size == 0):
                if trainThePolicy == True:
                    sess.run(update_grads,
                             feed_dict={w1_grad_ph: grad_buffer[0],
                                        w2_grad_ph: grad_buffer[1]})
                    grad_buffer = resetGradBuffer(grad_buffer)

                running_reward = reward_sum if running_reward is None else running_reward * 0.99 + reward_sum * 0.01

                if drawFromModel == False:
                    print('World Perf: Episode %f. Reward %f. action: %f. mean reward %f.' %
                          (real_episodes, reward_sum / real_bs, action, running_reward / real_bs))
                    if reward_sum / batch_size > 200:
                        print("Reward sum became too large")
                        break
                reward_sum = 0

                # Once the model has been trained on 100 episodes, we start alternating between training the policy
                # from the model and training the model from the real environment.
                if episode_number > 100:
                    drawFromModel = not drawFromModel
                    trainTheModel = not trainTheModel
                    trainThePolicy = not trainThePolicy

            # We were done, so reset the environment and move to next epoch
            if drawFromModel == True:
                observation = np.random.uniform(-0.1, 0.1, [4])  # Generate reasonable starting point
                batch_size = model_bs
            else:
                observation = env.reset()
                batch_size = real_bs

print(real_episodes)

