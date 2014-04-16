"""

The log-bilinear language model from (Mnih and Teh, ICML 2012)

References:
 A fast and simple algorithm for training neural probabilistic language models. 
 Andriy Mnih and Yee Whye Teh.
 International Conference on Machine Learning 2012 (ICML 2012) 

Usage: lbl.py [--verbose] [--word_dim WORD_DIM] [--context_sz CONTEXT_SZ] 
              [--learn_rate LEARN_RATE] [--rate_update RATE_UPDATE] 
              [--epochs EPOCHS] [--batch_size BATCH_SZ] [--seed SEED]  
              [--patience PATIENCE] [--patience_incr PATIENCE_INCR] 
              [--improvement_thrs IMPR_THRS] [--validation_freq VALID_FREQ] 
              <train_data> <dev_data> [<test_data>]

Arguments:
 train_data       training data of tokenized text, one sentence per line.
 dev_data         development data of tokenized text, one sentence per line.
 test_data        test data of tokenized text, one sentence per line.

Options:
    -v, --verbose                                Print debug information
    -k WORD_DIM, --word_dim=WORD_DIM             dimension of word embeddings [default: 100]
    -n CONTEXT_SZ, --context_sz=CONTEXT_SZ       size of n-gram context window [default: 2]
    -l LEARN_RATE, --learn_rate=LEARN_RATE       initial learning rate parameter [default: 1]
    -u RATE_UPDATE, --rate_update=RATE_UPDATE    learning rate update: 'simple', 'adaptive' [default: simple]
    -e EPOCHS, --epochs=EPOCHS                   number of training epochs [default: 10]
    -b BATCH_SIZE, --batch_size=BATCH_SIZE       size of mini-batch for training [default: 100]
    -s SEED, --seed=SEED                         seed for random generator.
    -p PATIENCE, --patience PATIENCE             min number of examples to look before stopping, default is no early stopping
    -i PATIENCE_INCR, --patience_incr=PATIENCE   wait for this much longer when a new best result is found [default: 2]
    -t IMPR_THRS, --improvement_thrs=IMPR_THRS   a relative improvemnt of this is considered significant [default: 0.995]
    -f VALID_FREQ, --validation_freq=VALID_FREQ  number of examples after which check score on dev set [default: 1000]
"""

from dataset import Dictionary, load_corpus

import cPickle
import numpy as np
import theano
import theano.tensor as T
import math
from future_builtins import zip
import time
import logging

logger = logging.getLogger(__name__)


class LogBilinearLanguageModel(object):
    """
    Log-bilinear language model class
    """

    def __init__(self, context, V, K, context_sz, rng):
        """
        Initialize the parameters of the language model
        """
        # training contexts
        self.context = context

        # initialize context word embedding matrix R of shape (V, K)
        # TODO: parameterize initialization
        R_values = np.asarray(rng.uniform(-0.01, 0.01, size=(V, K)), dtype=theano.config.floatX)
        self.R = theano.shared(value=R_values, name='R', borrow=True)

        # initialize target word embedding matrix Q of shape (V, K)
        Q_values = np.asarray(rng.uniform(-0.01, 0.01, size=(V, K)), dtype=theano.config.floatX)
        self.Q = theano.shared(value=Q_values, name='Q', borrow=True)

        # initialize weight tensor C of shape (context_sz, K, K)
        C_values = np.asarray(rng.normal(0, math.sqrt(0.1), size=(context_sz, K, K)), dtype=theano.config.floatX)
        self.C = theano.shared(value=C_values, name='C', borrow=True)

        # initialize bias vector 
        b_values = np.asarray(rng.normal(0, math.sqrt(0.1), size=(1,V)), dtype=theano.config.floatX)
        self.b = theano.shared(value=b_values, name='b', borrow=True)

         # r_w : context word representations
        self.r_w = self.R[context]
        # q_hat : predicted word representation for target word
        self.q_hat = T.tensordot(self.C, self.r_w, axes=[[0,1], [1,2]])
        # s_wh : similarity score between predicted word and all target words
        self.s = T.dot(self.Q, self.q_hat) + T.reshape(self.b, (V,1))
        # softmax activation function
        self.p_w_given_h = T.nnet.softmax(self.s)

        # compute prediction as class whose probability is maximal in
        # symbolic form
        self.y_pred = T.argmax(self.p_w_given_h, axis=1)

        # parameters of the model
        self.params = [self.R, self.Q, self.C, self.b]

    def negative_log_likelihood(self, y):
        # take the logarithm with base 2
        return -T.mean(T.log2(T.transpose(self.p_w_given_h))[T.arange(y.shape[0]),y])


    def errors(self, y):
        # check if y has same dimension of y_pred
        if y.ndim != self.y_pred.ndim:
            raise TypeError('y should have the same shape as self.y_pred',
                ('y', target.type, 'y_pred', self.y_pred.type))
        # check if y is of the correct datatype
        if y.dtype.startswith('int'):
            # the T.neq operator returns a vector of 0s and 1s, where 1
            # represents a mistake in prediction
            return T.mean(T.neq(self.y_pred, y))
        else:
            raise NotImplementedError()
        
def make_instances(corpus, vocab, context_sz):
    def shared_dataset(data_xy, borrow=True):
        data_x, data_y = data_xy
        shared_x = theano.shared(np.asarray(data_x, dtype=np.int32), borrow=borrow)
        shared_y = theano.shared(np.asarray(data_y, dtype=np.int32), borrow=borrow)
        return shared_x, shared_y
    data = []
    labels = []        
    for sentence in corpus:
        # add 'start of sentence' and 'end of sentence' context
        sentence = ['<s>'] * context_sz + sentence + ['</s>'] * context_sz
        sentence = vocab.doc_words_to_ids(sentence, update_dict=False, unk='<unk>')
        for instance in zip(*(sentence[i:] for i in xrange(context_sz+1))):
            data.append(instance[:-1])
            labels.append(instance[-1])

    train_set_x, train_set_y = shared_dataset([data, labels])
    return train_set_x, train_set_y


def train_lbl(train_data, dev_data, test_data=[], 
              K=20, context_sz=2, learning_rate=1.0, 
              rate_update='simple', epochs=10, 
              batch_size=100, rng=None, patience=None, 
              patience_incr=2, improvement_thrs=0.995, 
              validation_freq=1000):
    """
    Train log-bilinear model
    """
    # create vocabulary from train data, plus <s>, </s>
    vocab = Dictionary.from_corpus(train_data)
    vocab.lookup_id('<s>', update_dict = True)
    vocab.lookup_id('</s>', update_dict = True)
    V = vocab.size()

    # initialize random generator if not provided
    rng = np.random.RandomState() if not rng else rng

    # generate (context, target) pairs of word ids
    train_set_x, train_set_y = make_instances(train_data, vocab, context_sz)
    dev_set_x, dev_set_y = make_instances(dev_data, vocab, context_sz)
    test_set_x, test_set_y = make_instances(test_data, vocab, context_sz)

    # number of minibatches for training
    n_train_batches = train_set_x.get_value(borrow=True).shape[0] / batch_size
    n_dev_batches = dev_set_x.get_value(borrow=True).shape[0] / batch_size
    n_test_batches = test_set_x.get_value(borrow=True).shape[0] / batch_size

    # build the model
    logger.info("Build the model ...")
    index = T.lscalar()
    x = T.imatrix('x')
    y = T.ivector('y')
    
    # create log-bilinear model
    lbl = LogBilinearLanguageModel(x, V, K, context_sz, rng)

    # cost function during training is negative log likelihood of the training data
    cost = lbl.negative_log_likelihood(y)

    # compute the gradient
    gparams = []
    for param in lbl.params:
        gparam = T.grad(cost, param)
        gparams.append(gparam)

    # specify how to update the parameter of the model
    updates = []
    for param, gparam in zip(lbl.params, gparams):
        updates.append((param, param-learning_rate*gparam))

    # compiling theano functions that computes log-probability of the dev set
    logprob_dev = theano.function(inputs=[index], outputs=cost,
                                 givens={x: dev_set_x[index*batch_size:(index+1)*batch_size],
                                         y: dev_set_y[index*batch_size:(index+1)*batch_size]
                                         })


    # validate_model = theano.function(inputs=[index],
    #                                  outputs=lbl.errors(y),
    #                                  givens={ x: dev_set_x[index * batch_size:(index + 1) * batch_size],
    #                                           y: dev_set_y[index * batch_size:(index + 1) * batch_size]})

    # compiling theano functions that computes log-probability of the test set
    logprob_test = theano.function(inputs=[index], outputs=cost,
                                 givens={x: test_set_x[index*batch_size:(index+1)*batch_size],
                                         y: test_set_y[index*batch_size:(index+1)*batch_size]
                                         })
    
    # compiling theano function 'train model' that returns the cost
    # and updates the parameter of the model 
    train_model = theano.function(inputs=[index], outputs=cost,
                                  updates=updates,
                                  givens={x: train_set_x[index*batch_size:(index+1)*batch_size],
                                          y: train_set_y[index*batch_size:(index+1)*batch_size]
                                          })

    # perplexity functions
    def compute_dev_logp():
        return np.mean([logprob_dev(i) for i in xrange(n_dev_batches)])

    def compute_dev_ppl():
        return np.power(2.0, compute_dev_logp())

    def compute_test_logp():
        return np.mean([logprob_test(i) for i in xrange(n_test_batches)])

    def compute_test_ppl():
        return np.power(2.0, compute_test_logp())
        
    # train model
    logger.info("training model...")
    best_params = None
    last_epoch_dev_ppl = np.inf
    best_dev_ppl = np.inf
    test_ppl = np.inf
    test_core = 0
    start_time = time.clock()
    done_looping = False

    for epoch in xrange(epochs):
        if done_looping:
            break
        logger.debug('epoch %i' % epoch) 
        for minibatch_index in xrange(n_train_batches):
            minibatch_avg_cost  = train_model(minibatch_index)
            # iteration number
            itr = epoch * n_train_batches + minibatch_index            
            if itr % validation_freq == 0:
                # compute perplexity on dev set, lower is better
                dev_logp = compute_dev_logp()
                dev_ppl = compute_dev_ppl()
                logger.debug('epoch %i, minibatch %i/%i, dev ppl %.4f' % (epoch, minibatch_index+1,
                                                                                   n_train_batches, dev_ppl))
                logger.debug('epoch %i, minibatch %i/%i, dev log prob %.4f' % (epoch, minibatch_index+1,
                                                                                   n_train_batches, dev_logp))

                validation_losses = [validate_model(i) for i in xrange(n_valid_batches)]
                this_validation_loss = numpy.mean(validation_losses)
                logger.debug('epoch %i, minibatch %i/%i, dev log prob %.4f' % (epoch, minibatch_index+1, this_validation_loss))


                # if we got the lowest perplexity until now
                if dev_ppl < best_dev_ppl:
                    # improve patience if loss improvement is good enough
                    if patience and dev_ppl < best_dev_ppl * improvement_thrs:
                        patience = max(patience, itr * patience_incr)
                    best_dev_ppl = dev_ppl
                    # test perplexity
                    test_logp = compute_test_logp()
                    test_ppl = compute_test_ppl()
                    logger.debug('  epoch %i, minibatch %i/%i, test ppl %.4f' % (epoch, minibatch_index+1, 
                                                                                  n_train_batches, test_ppl))
                    logger.debug('epoch %i, minibatch %i/%i, test log prob %.4f' % (epoch, minibatch_index+1,
                                                                                    n_train_batches, test_logp))
            # stop learning if no improvement was seen for a long time
            if patience and patience <= itr:
                done_looping = True
                break
        # adapt learning rate
        if rate_update == 'simple':
            # set learning rate to 1 / epoch index (1, 0.5, 0.33, ..)
            learning_rate = 1.0 / (epoch+1)
        elif rate_update == 'adaptive':
            # half learning rate if perplexity increased at end of epoch
            this_epoch_dev_ppl = compute_dev_ppl()
            if this_epoch_dev_ppl > last_epoch_dev_ppl:
                learning_rate /= 2.0
            last_epoch_dev_ppl = this_epoch_dev_ppl
        elif rate_update == 'constant':
            # keep learning rate constant
            pass
        else:
            raise ValueError("Unknown learning rate update strategy: %s" %rate_update)
        

    end_time = time.clock()
    total_time = end_time - start_time
    logger.info('Optimization complete with best dev ppl of %.4f and test ppl %.4f' % (best_dev_ppl, test_ppl))
    logger.info('Training took %d epochs, with %.1f epochs/sec' % (epoch+1, 
                float(epoch+1) / total_time))
    logger.info("Total training time %d days %d hours %d min %d sec." % (total_time/60/60/24, total_time/60/60%24, total_time/60%60, total_time%60))

    
if __name__ == '__main__':
    import sys
    from docopt import docopt

    # parse command line arguments
    arguments = docopt(__doc__)
    log_level= logging.DEBUG if arguments['--verbose'] else logging.INFO
    FORMAT = '%(asctime)-15s %(message)s'
    logging.basicConfig(format=FORMAT)
    logger.setLevel(log_level)
    word_dim = int(arguments['--word_dim'])
    context_sz = int(arguments['--context_sz'])
    learn_rate = float(arguments['--learn_rate'])
    rate_update = arguments['--rate_update']
    epochs = int(arguments['--epochs'])
    batch_sz = int(arguments['--batch_size'])
    seed = int(arguments['--seed']) if arguments['--seed'] else None
    patience = int(arguments['--patience']) if arguments['--patience'] else None
    patience_incr = int(arguments['--patience_incr'])
    improvement_thrs = float(arguments['--improvement_thrs'])
    validation_freq = int(arguments['--validation_freq'])

    # load data
    logger.info("Load data ...")
    with open(arguments['<train_data>'], 'rb') as fin:
        train_data = [line.split() for line in fin.readlines() if line.strip()]    
    with open(arguments['<dev_data>'], 'rb') as fin:
        dev_data = [line.split() for line in fin.readlines() if line.strip()]
    if arguments['<test_data>']:
        with open(arguments['<test_data>'], 'rb') as fin:
            test_data = [line.split() for line in fin.readlines() if line.strip()]
    else:
        test_data = []

    # create random number generator
    rng_state = np.random.RandomState(seed)

    # train lm model
    train_lbl(train_data, dev_data, test_data=test_data, 
              K=word_dim, context_sz=context_sz, learning_rate=learn_rate, 
              rate_update=rate_update, epochs=epochs, batch_size = batch_sz, 
              rng=rng_state, patience=patience, patience_incr=patience_incr, 
              improvement_thrs=improvement_thrs, validation_freq=validation_freq)
              

