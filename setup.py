from setuptools import setup

# Define DUMMY to force git push
DUMMY = 1

setup(name='MetroEscalators', version='1.0',
      description='WMATA Escalator Data',
      author='Lee Mendelowitz', author_email='MetroEscalators@gmail.com',
      url='http://www.python.org/sigs/distutils-sig/',

      #  Uncomment one or more lines below in the install_requires section
      #  for the specific client drivers/modules your application needs.
      install_requires=['greenlet', 'gevent',
                           'tweepy',
                           'requests',
                        #  'MySQL-python',
                           'pymongo',
                        #  'psycopg2',
                           'python-dateutil==1.5'
      ],
     )
